"""Provider 管理器（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.provider.manager.ProviderManager` 的常用接口，
Provider 实例数据全部来自宿主 HostService（ListProviders/GetUsingProvider/
SetProvider），本模块负责拉取、包装（Provider 对象）与切换转发。

插件侧典型用法：
    await self.context.provider_manager.set_provider(
        provider_id=id_,
        provider_type=ProviderType.CHAT_COMPLETION,
        umo=umo,
    )
    self.context.provider_manager.personas   # 人格列表（/persona 命令用）
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from astrbot.core.provider.entities import ProviderType
from astrbot.core.provider.provider import Provider, STTProvider, TTSProvider
from astrbot.core.utils.error_redaction import safe_error

logger = logging.getLogger("astrbot")

# ProviderType 枚举 → 宿主能力字符串（用于 ListProviders/GetUsingProvider/SetProvider）
_PROVIDER_TYPE_TO_CAPABILITY: dict[ProviderType, str] = {
    ProviderType.CHAT_COMPLETION: "chat_completion",
    ProviderType.TEXT_TO_SPEECH: "text_to_speech",
    ProviderType.SPEECH_TO_TEXT: "speech_to_text",
    ProviderType.EMBEDDING: "embedding",
    ProviderType.RERANK: "rerank",
}


def _to_capability(provider_type: ProviderType | str) -> str:
    """把 ProviderType 枚举或能力字符串归一化为宿主能力字符串。"""
    if isinstance(provider_type, ProviderType):
        return _PROVIDER_TYPE_TO_CAPABILITY.get(provider_type, provider_type.value)
    return str(provider_type)


class ProviderManager:
    def __init__(self, bridge: Any | None = None) -> None:
        # bridge 可以是 HostBridge 实例，也可以是返回 HostBridge 的
        # 可调用对象（Context 传入 self._bridge 保持单桥来源一致）
        self._bridge_getter: Any = bridge
        self.default_persona_name: str = "default"
        # 人格相关属性（v4.0.0 后废弃但仍被插件读取）
        self._provider_change_callback: Callable[[str, ProviderType, str | None], None] | None = None
        self._provider_change_hooks: list[Callable[[str, ProviderType, str | None], None]] = []

    def _bridge(self):
        if self._bridge_getter is None:
            raise RuntimeError("宿主桥未就绪（ProviderManager 未绑定宿主）")
        if callable(self._bridge_getter):
            return self._bridge_getter()
        return self._bridge_getter

    # ── 人格（对齐本体 provider_manager.personas，/persona 命令用）──────────
    @property
    def personas(self) -> list[dict]:
        """人格 dict 列表（含 "name"/"prompt" 键，插件按 persona["name"] 访问）。

        宿主字段为 persona_id/name/system_prompt/...，这里归一化补上
        "prompt"（= system_prompt 的别名，对齐本体 v3 人格配置结构）。
        """
        try:
            raw = self._bridge().get_personas()
        except Exception as e:
            logger.warning(f"provider_manager.personas 拉取失败: {e}")
            return []
        out: list[dict] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            norm = dict(item)
            if "prompt" not in norm and norm.get("system_prompt"):
                norm["prompt"] = norm["system_prompt"]
            if "name" not in norm and norm.get("persona_id"):
                norm["name"] = norm["persona_id"]
            out.append(norm)
        return out

    @property
    def persona_configs(self) -> list[dict]:
        """人格配置 dict 列表（= personas 的原始形态）。"""
        return self.personas

    # ── Provider 实例列表 ──────────────────────────────────────────────────
    def _list_providers(self, capability: str = "") -> list[Provider]:
        try:
            raw = self._bridge().list_providers(capability)
        except Exception as e:
            logger.warning(f"list_providers({capability}) 失败: {e}")
            return []
        return [
            Provider(item, _bridge_getter=self._bridge)
            for item in raw
            if isinstance(item, dict)
        ]

    @property
    def provider_insts(self) -> list[Provider]:
        """已加载的 Chat（文本生成）Provider 实例列表。"""
        return self._list_providers("chat_completion")

    @property
    def chat_provider_insts(self) -> list[Provider]:
        """chat_provider_insts 别名（对齐本体命名）。"""
        return self.provider_insts

    @property
    def tts_provider_insts(self) -> list[TTSProvider]:
        """已加载的 TTS Provider 实例列表。"""
        return self._list_providers("text_to_speech")

    @property
    def stt_provider_insts(self) -> list[STTProvider]:
        """已加载的 STT Provider 实例列表。"""
        return self._list_providers("speech_to_text")

    @property
    def inst_map(self) -> dict[str, Provider]:
        """Provider 实例映射（key: provider_id）。每次访问向宿主拉取最新列表。"""
        return {p.meta_id: p for p in self._list_providers()}

    def get_insts(self) -> list[Provider]:
        return self.provider_insts

    async def get_provider_by_id(self, provider_id: str) -> Provider | None:
        """根据提供商 ID 获取 Provider 实例。"""
        return await asyncio.to_thread(self._get_provider_by_id, provider_id)

    def _get_provider_by_id(self, provider_id: str) -> Provider | None:
        return self.inst_map.get(provider_id)

    # ── 当前使用的 Provider ────────────────────────────────────────────────
    def get_using_provider(
        self,
        capability: str = "chat_completion",
        umo: str | None = None,
    ) -> Provider | None:
        """获取正在使用的 Provider 实例（同步；宿主桥同步 RPC）。

        capability: chat_completion / text_to_speech / speech_to_text。
        """
        try:
            data = self._bridge().get_using_provider(umo or "", capability)
        except Exception as e:
            logger.warning(f"get_using_provider({capability}) 失败: {e}")
            return None
        if not isinstance(data, dict) or not data:
            return None
        return Provider(data, _bridge_getter=self._bridge)

    async def get_using_provider_async(
        self,
        capability: str = "chat_completion",
        umo: str | None = None,
    ) -> Provider | None:
        """获取正在使用的 Provider 实例（异步版）。"""
        try:
            data = await self._bridge().get_using_provider_async(umo or "", capability)
        except Exception as e:
            logger.warning(f"get_using_provider_async({capability}) 失败: {e}")
            return None
        if not isinstance(data, dict) or not data:
            return None
        return Provider(data, _bridge_getter=self._bridge)

    # ── 切换 Provider ──────────────────────────────────────────────────────
    async def set_provider(
        self,
        provider_id: str,
        provider_type: ProviderType,
        umo: str | None = None,
    ) -> None:
        """设置提供商（对齐本体签名：provider_id + ProviderType 枚举 + umo）。

        对齐本体语义：提供商不存在时抛 ValueError；切换成功后通知
        provider 变更回调（插件用它失效模型列表缓存）。
        """
        capability = _to_capability(provider_type)
        if capability not in (
            "chat_completion",
            "text_to_speech",
            "speech_to_text",
        ):
            raise ValueError(f"不支持的提供商类型: {capability}")
        if provider_id not in self.inst_map:
            raise ValueError(
                f"Provider {provider_id} does not exist and cannot be set."
            )
        try:
            ok = await self._bridge().set_provider_async(
                umo or "",
                provider_id,
                capability,
            )
        except Exception as e:
            logger.warning(f"set_provider({provider_id}) 失败: {safe_error('', e)}")
            ok = False
        if not ok:
            raise ValueError(
                f"Provider {provider_id} does not exist and cannot be set."
            )
        self._notify_provider_changed(provider_id, provider_type, umo)

    # ── Provider 变更回调（插件用于失效模型列表缓存）───────────────────────
    def set_provider_change_callback(
        self,
        cb: Callable[[str, ProviderType, str | None], None] | None,
    ) -> None:
        """设置单个 provider 变更回调（向后兼容本体）。"""
        self._provider_change_callback = cb

    def register_provider_change_hook(
        self,
        hook: Callable[[str, ProviderType, str | None], None],
    ) -> None:
        """注册 provider 变更钩子。"""
        if hook not in self._provider_change_hooks:
            self._provider_change_hooks.append(hook)

    def _notify_provider_changed(
        self,
        provider_id: str,
        provider_type: ProviderType,
        umo: str | None,
    ) -> None:
        if self._provider_change_callback is not None:
            try:
                self._provider_change_callback(provider_id, provider_type, umo)
            except Exception as e:
                logger.warning(
                    "Provider change callback failed: provider_id=%s, type=%s, err=%s",
                    provider_id,
                    provider_type,
                    safe_error("", e),
                )
        for hook in list(self._provider_change_hooks):
            if hook is self._provider_change_callback:
                continue
            try:
                hook(provider_id, provider_type, umo)
            except Exception as e:
                logger.warning(
                    "Provider change hook failed: provider_id=%s, type=%s, err=%s",
                    provider_id,
                    provider_type,
                    safe_error("", e),
                )
