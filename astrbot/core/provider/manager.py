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
from astrbot.core.provider.provider import (
    EmbeddingProvider,
    Provider,
    RerankProvider,
    STTProvider,
    TTSProvider,
)
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

# 宿主能力字符串 → 包装类（list_providers/get_using_provider 据此构造实例）
_PROVIDER_CLS_MAP: dict[str, type] = {
    "chat_completion": Provider,
    "text_to_speech": TTSProvider,
    "speech_to_text": STTProvider,
    "embedding": EmbeddingProvider,
    "rerank": RerankProvider,
}


def _to_capability(provider_type: ProviderType | str) -> str:
    """把 ProviderType 枚举或能力字符串归一化为宿主能力字符串。"""
    if isinstance(provider_type, ProviderType):
        return _PROVIDER_TYPE_TO_CAPABILITY.get(provider_type, provider_type.value)
    return str(provider_type)


def _wrap_provider(data: dict, capability: str, bridge) -> Provider:
    """按宿主能力字符串选择包装类构造 Provider 实例。"""
    cls = _PROVIDER_CLS_MAP.get(capability, Provider)
    return cls(data, _bridge_getter=bridge)


class ProviderManager:
    def __init__(self, bridge: Any | None = None) -> None:
        # bridge 可以是 HostBridge 实例，也可以是返回 HostBridge 的
        # 可调用对象（Context 传入 self._bridge 保持单桥来源一致）
        self._bridge_getter: Any = bridge
        self.default_persona_name: str = "default"
        # 人格相关属性（v4.0.0 后废弃但仍被插件读取）
        self._provider_change_callback: Callable[[str, ProviderType, str | None], None] | None = None
        self._provider_change_hooks: list[Callable[[str, ProviderType, str | None], None]] = []
        # 当前使用中的 Provider 实例缓存（get_using_provider 结果缓存，
        # 对齐本体 provider_manager.curr_*_provider_inst 属性）
        self.curr_provider_inst: Provider | None = None
        self.curr_stt_provider_inst: STTProvider | None = None
        self.curr_tts_provider_inst: TTSProvider | None = None

    def _bridge(self):
        if self._bridge_getter is None:
            raise RuntimeError("宿主桥未就绪（ProviderManager 未绑定宿主）")
        if callable(self._bridge_getter):
            return self._bridge_getter()
        return self._bridge_getter

    @property
    def llm_tools(self):
        """LLM 函数工具注册表（对齐本体 provider_manager.llm_tools）。

        返回 astrbot.core.provider.func_tool_manager 的 llm_tools 单例，
        插件可用 `provider_manager.llm_tools.remove_func(...)` 等。
        """
        from astrbot.core.provider.func_tool_manager import llm_tools

        return llm_tools

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

    @property
    def selected_default_persona(self):
        """默认选中的人格 dict（已弃用，建议用 context.persona_mgr 获取）。"""
        try:
            raw = self._bridge().get_default_persona("")
        except Exception as e:
            logger.warning(f"provider_manager.selected_default_persona 拉取失败: {e}")
            return {"name": self.default_persona_name}
        if isinstance(raw, dict) and raw:
            return raw
        return {"name": self.default_persona_name}

    # ── Provider 实例列表 ──────────────────────────────────────────────────
    def _list_providers(self, capability: str = "") -> list[Provider]:
        try:
            raw = self._bridge().list_providers(capability)
        except Exception as e:
            logger.warning(f"list_providers({capability}) 失败: {e}")
            return []
        return [
            _wrap_provider(item, capability, self._bridge)
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
    def embedding_provider_insts(self) -> list[EmbeddingProvider]:
        """已加载的 Embedding Provider 实例列表（对齐本体同名属性）。"""
        return self._list_providers("embedding")

    @property
    def rerank_provider_insts(self) -> list[RerankProvider]:
        """已加载的 Rerank Provider 实例列表（对齐本体同名属性）。"""
        return self._list_providers("rerank")

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
    def _cache_curr_provider(self, inst: Provider | None, capability: str) -> None:
        """按能力把 get_using_provider 的结果缓存到对应 curr_* 属性。"""
        if capability == "chat_completion":
            self.curr_provider_inst = inst
        elif capability == "speech_to_text":
            self.curr_stt_provider_inst = inst
        elif capability == "text_to_speech":
            self.curr_tts_provider_inst = inst

    def get_using_provider(
        self,
        capability: ProviderType | str = "chat_completion",
        umo: str | None = None,
    ) -> Provider | None:
        """获取正在使用的 Provider 实例（同步；宿主桥同步 RPC）。

        第一参数兼容两种传法（对齐本体签名）：
        - ProviderType 枚举（如 ProviderType.CHAT_COMPLETION → "chat_completion"）
        - 宿主能力字符串（如 "chat_completion"，SDK 原有用法）

        结果缓存到 curr_provider_inst / curr_stt_provider_inst /
        curr_tts_provider_inst 对应属性。
        """
        capability = _to_capability(capability)
        try:
            data = self._bridge().get_using_provider(umo or "", capability)
        except Exception as e:
            logger.warning(f"get_using_provider({capability}) 失败: {e}")
            return None
        inst = None
        if isinstance(data, dict) and data:
            inst = _wrap_provider(data, capability, self._bridge)
        self._cache_curr_provider(inst, capability)
        return inst

    async def get_using_provider_async(
        self,
        capability: ProviderType | str = "chat_completion",
        umo: str | None = None,
    ) -> Provider | None:
        """获取正在使用的 Provider 实例（异步版，签名同 get_using_provider）。"""
        capability = _to_capability(capability)
        try:
            data = await self._bridge().get_using_provider_async(umo or "", capability)
        except Exception as e:
            logger.warning(f"get_using_provider_async({capability}) 失败: {e}")
            return None
        inst = None
        if isinstance(data, dict) and data:
            inst = _wrap_provider(data, capability, self._bridge)
        self._cache_curr_provider(inst, capability)
        return inst

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
        # 用异步 RPC 拉取列表，避免 inst_map 同步 gRPC（最长 30s）阻塞事件循环
        try:
            insts = await self._bridge().list_providers_async()
        except Exception as e:
            logger.warning(f"list_providers 失败: {e}")
            insts = []
        if provider_id not in {
            str(item.get("id") or item.get("meta_id") or "")
            for item in insts
            if isinstance(item, dict)
        }:
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
        # 切换成功后失效对应能力的当前 Provider 缓存
        self._cache_curr_provider(None, capability)
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

    # ── 管理方法（宿主 Provider 原生管理，SDK 薄壳对齐本体方法面）────────────
    async def initialize(self) -> None:
        """初始化全部 Provider（SDK 薄壳：宿主已初始化，no-op）。"""

    async def load_provider(self, provider_config: dict) -> None:
        """实例化一个 Provider（SDK 薄壳：宿主原生管理，no-op）。"""

    async def reload(self, provider_config: dict) -> None:
        """重载 Provider（SDK 薄壳：宿主原生管理，no-op）。"""

    async def terminate_provider(self, provider_id: str) -> None:
        """终止一个 Provider（SDK 薄壳：宿主原生管理，no-op）。"""

    async def delete_provider(
        self,
        provider_id: str | None = None,
        provider_source_id: str | None = None,
    ) -> None:
        """删除 Provider（SDK 薄壳：宿主原生管理，no-op）。"""

    async def update_provider(self, origin_provider_id: str, new_config: dict) -> None:
        """更新 Provider（SDK 薄壳：宿主原生管理，no-op）。"""

    async def create_provider(self, new_config: dict) -> None:
        """创建 Provider（SDK 薄壳：宿主原生管理，no-op）。"""

    async def terminate(self) -> None:
        """终止全部 Provider（SDK 薄壳：宿主原生管理，no-op）。"""
