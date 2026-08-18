"""Provider 类（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.provider.provider.Provider` 的常用接口：
- `provider.meta()` 返回 ProviderMeta（id/model/type/provider_type）
- `provider.set_model()/get_model()` 管理当前模型名
- `await provider.get_models()` 经宿主桥获取模型列表
- `await provider.test()` 可达性测试（宿主无直连通道，直接返回 True）

实例数据来自宿主 ListProviders/GetUsingProvider 返回的
`{"id", "model", "type", "provider_type"}` dict。
"""
from __future__ import annotations

import logging
from typing import Any

from astrbot.core.provider.entities import ProviderMeta, ProviderType

logger = logging.getLogger("astrbot")

# 能力字符串 → ProviderType 枚举
_PROVIDER_TYPE_MAP: dict[str, ProviderType] = {
    "chat_completion": ProviderType.CHAT_COMPLETION,
    "text_to_speech": ProviderType.TEXT_TO_SPEECH,
    "speech_to_text": ProviderType.SPEECH_TO_TEXT,
    "embedding": ProviderType.EMBEDDING,
    "rerank": ProviderType.RERANK,
}


def _to_provider_type(value: Any) -> ProviderType:
    """把宿主返回的能力字符串（或 ProviderType）归一化为 ProviderType。"""
    if isinstance(value, ProviderType):
        return value
    if isinstance(value, str):
        return _PROVIDER_TYPE_MAP.get(value, ProviderType.CHAT_COMPLETION)
    return ProviderType.CHAT_COMPLETION


class Provider:
    """Chat Provider（文本生成任务）。"""

    def __init__(
        self,
        provider_config: dict | None = None,
        provider_settings: dict | None = None,
        **kwargs: Any,
    ) -> None:
        if provider_config is None:
            provider_config = kwargs or {}
        self.provider_config: dict = provider_config or {}
        self.provider_settings: dict = provider_settings or {}
        # 宿主桥来源：优先实例自带（ProviderManager 构造时注入），
        # 否则回退全局（context.get_host_bridge）
        self._bridge_getter: Any = kwargs.pop("_bridge_getter", None)
        # 兼容两种数据键：宿主 ListProviders 用 "id"，部分插件构造时用 "meta_id"
        self.meta_id: str = str(
            self.provider_config.get("id")
            or self.provider_config.get("meta_id")
            or ""
        )
        self.model_name: str = str(self.provider_config.get("model") or "")
        self.type: str = str(self.provider_config.get("type") or "")
        self.provider_type: ProviderType = _to_provider_type(
            self.provider_config.get("provider_type")
        )

    def _bridge(self):
        if self._bridge_getter is not None:
            if callable(self._bridge_getter):
                return self._bridge_getter()
            return self._bridge_getter
        from astrbot.core.star.context import get_host_bridge

        return get_host_bridge()

    @property
    def id(self) -> str:
        """提供商 ID（= meta_id）。"""
        return self.meta_id

    def meta(self) -> ProviderMeta:
        """获取提供商元数据。"""
        return ProviderMeta(
            id=self.meta_id,
            model=self.get_model(),
            type=self.type,
            provider_type=self.provider_type,
        )

    def set_model(self, model_name: str) -> None:
        """设置当前模型名。"""
        self.model_name = model_name or ""

    def get_model(self) -> str:
        """获取当前模型名。"""
        return self.model_name or ""

    async def get_models(self) -> list[str]:
        """获得支持的模型列表（经宿主桥；宿主未实现时返回 []）。"""
        try:
            bridge = self._bridge()
            if bridge is None:
                return []
            models = await bridge.get_provider_models_async(self.meta_id)
            return list(models) if models else []
        except Exception as e:
            logger.warning(f"get_models({self.meta_id}) 失败: {e}")
            return []

    async def text_chat_stream(self, **kwargs) -> Any:
        """流式 LLM 调用（降级实现）。

        Go 宿主桥无流式 RPC：经 chat_llm_async 拿完整文本后，yield 一个
        LLMResponse（对齐本体流式响应对象字段，最后一条为完整结果）。
        插件 `async for resp in provider.text_chat_stream(...)` 至少能拿到完整回复。

        Args:
            prompt: 提示词
            system_prompt: 系统提示词
            image_urls: 图片 URL 列表
            session_id: 会话 ID
        """
        from astrbot.core.provider.entities import LLMResponse

        prompt = kwargs.get("prompt") or ""
        system_prompt = kwargs.get("system_prompt") or ""
        image_urls = kwargs.get("image_urls") or []
        session_id = kwargs.get("session_id") or ""
        # contexts 传入时宿主无对应通道，取最后一条文本兜底为 prompt
        if not prompt:
            contexts = kwargs.get("contexts") or []
            if contexts and isinstance(contexts[-1], dict) and contexts[-1].get("content"):
                prompt = str(contexts[-1]["content"])
        bridge = self._bridge()
        if bridge is None:
            raise RuntimeError("宿主桥未就绪（text_chat_stream 不可用）")
        text = await bridge.chat_llm_async(
            prompt,
            system_prompt,
            image_urls,
            session_id,
        )
        yield LLMResponse(role="assistant", completion_text=text)

    async def test(self) -> bool:
        """可达性测试。Go 宿主无 Provider 直连通道，直接视为可用。"""
        return True

    def get_current_key(self) -> str:
        return ""

    def get_keys(self) -> list[str]:
        keys = self.provider_config.get("key", [""])
        return list(keys) if keys else [""]

    def set_key(self, key: str) -> None:
        self.provider_config["key"] = [key]

    def __repr__(self) -> str:
        return f"Provider(id={self.meta_id!r}, model={self.get_model()!r}, type={self.type!r})"


class TTSProvider(Provider):
    """Text-to-Speech Provider（宿主 ListProviders 返回 text_to_speech 能力）。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        raw_pt = self.provider_config.get("provider_type")
        if not isinstance(raw_pt, str) or not raw_pt:
            self.provider_type = ProviderType.TEXT_TO_SPEECH


class STTProvider(Provider):
    """Speech-to-Text Provider（宿主 ListProviders 返回 speech_to_text 能力）。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        raw_pt = self.provider_config.get("provider_type")
        if not isinstance(raw_pt, str) or not raw_pt:
            self.provider_type = ProviderType.SPEECH_TO_TEXT


Providers = (Provider, TTSProvider, STTProvider)
