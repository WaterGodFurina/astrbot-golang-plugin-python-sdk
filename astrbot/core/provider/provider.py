"""Provider 类（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.provider.provider.Provider` 的常用接口：
- `provider.meta()` 返回 ProviderMeta（id/model/type/provider_type）
- `provider.set_model()/get_model()` 管理当前模型名
- `await provider.get_models()` 经宿主桥获取模型列表
- `await provider.text_chat()/text_chat_stream()` LLM 文本生成（降级：宿主桥 chat_llm_async）
- `await provider.test()` 可达性测试（经宿主桥发 "REPLY PONG ONLY"）

实例数据来自宿主 ListProviders/GetUsingProvider 返回的
`{"id", "model", "type", "provider_type"}` dict。
"""
from __future__ import annotations

import abc
import asyncio
import logging
import os
from typing import Any

import httpx

from astrbot.core.agent.message import ContentPart, Message, is_checkpoint_message
from astrbot.core.provider.entities import (
    ProviderMeta,
    ProviderMetaData,
    ProviderType,
    RerankResult,
    ToolCallsResult,
)
from astrbot.core.utils.astrbot_path import get_astrbot_path

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


class AbstractProvider(abc.ABC):
    """Provider 抽象基类（对齐 Python 本体 provider.py 顶部定义）。

    提供 meta/config/settings/model 相关属性的默认实现（不抛
    NotImplementedError），子类可覆盖 test() 等运行期方法（run 类操作
    可抛 NotImplementedError 提示未实现）。
    """

    def __init__(self, provider_config: dict | None = None) -> None:
        super().__init__()
        self.model_name = ""
        self.provider_config: dict = provider_config or {}

    def set_model(self, model_name: str) -> None:
        """设置当前模型名。"""
        self.model_name = model_name or ""

    def get_model(self) -> str:
        """获取当前模型名。"""
        return self.model_name or ""

    def meta(self) -> ProviderMeta:
        """获取提供商元数据（类型名取自 provider_config["type"]）。"""
        provider_type_name = str(self.provider_config.get("type") or "chat_completion")
        meta_data = provider_cls_map.get(provider_type_name)
        provider_type = (
            meta_data.provider_type
            if meta_data is not None
            else _to_provider_type(provider_type_name)
        )
        return ProviderMeta(
            id=str(self.provider_config.get("id", "default")),
            model=self.get_model(),
            type=provider_type_name,
            provider_type=provider_type,
        )

    async def test(self) -> bool:
        """可达性测试（占位实现：不抛异常，返回 True）。"""
        return True


class Provider(AbstractProvider):
    """Chat Provider（文本生成任务）。"""

    def __init__(
        self,
        provider_config: dict | None = None,
        provider_settings: dict | None = None,
        **kwargs: Any,
    ) -> None:
        if provider_config is None:
            provider_config = kwargs or {}
        super().__init__(provider_config or {})
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

    @staticmethod
    def _build_llm_response(text: str) -> Any:
        """把宿主桥返回的纯文本包装成 LLMResponse（role=assistant + result_chain=[Plain]）。

        对齐本体 LLMResponse 结构：result_chain 为 MessageChain([Plain(text)])，
        其 completion_text 属性会经 result_chain 自动取到纯文本。
        """
        from astrbot.core.message.components import Plain
        from astrbot.core.message.message_event_result import MessageChain
        from astrbot.core.provider.entities import LLMResponse

        resp = LLMResponse(role="assistant")
        resp.result_chain = MessageChain([Plain(text or "")])
        return resp

    async def text_chat(
        self,
        prompt: str | None = None,
        system_prompt: str = "",
        image_urls: list[str] | None = None,
        session_id: str = "",
        func_tool: Any | None = None,
        tool_choice: Any | None = None,
        request_max_retries: int | None = 3,
        **kwargs: Any,
    ) -> Any:
        """非流式 LLM 调用（降级实现）。

        Go 宿主桥无非流式专用 RPC：复用 chat_llm_async 拿完整文本后，
        包装为 `LLMResponse(role="assistant") + result_chain=[Plain(text)]`
        （对齐本体 text_chat 的返回对象）。

        Args:
            prompt: 提示词
            system_prompt: 系统提示词
            image_urls: 图片 URL 列表
            session_id: 会话 ID（宿主桥透传，语义由宿主决定）
            func_tool: 函数工具集合（宿主桥无对应通道，忽略）
            tool_choice: 工具调用策略（宿主桥无对应通道，忽略）
            request_max_retries: 最大重试次数（宿主桥无对应通道，忽略）
            **kwargs: 其他参数（含 contexts：存在时取末条文本拼到 prompt 前，
                参考 Context.llm_generate 的现有做法）
        """
        prompt = prompt or ""
        # contexts 传入时宿主无对应通道，取最后一条文本拼到 prompt 前
        contexts = kwargs.get("contexts") or []
        if contexts and isinstance(contexts[-1], dict) and contexts[-1].get("content"):
            prompt = str(contexts[-1]["content"]) + "\n" + prompt
        bridge = self._bridge()
        if bridge is None:
            raise RuntimeError("宿主桥未就绪（text_chat 不可用）")
        text = await bridge.chat_llm_async(
            prompt,
            system_prompt or "",
            image_urls or [],
            session_id or "",
        )
        return self._build_llm_response(text)

    async def text_chat_stream(
        self,
        prompt: str | None = None,
        system_prompt: str = "",
        image_urls: list[str] | None = None,
        audio_urls: list[str] | None = None,
        session_id: str = "",
        func_tool: Any | None = None,
        tool_choice: Any | None = None,
        contexts: list | None = None,
        request_max_retries: int | None = None,
        **kwargs: Any,
    ) -> Any:
        """流式 LLM 调用（降级实现）。

        Go 宿主桥无流式 RPC：经 chat_llm_async 拿完整文本后，yield 一个
        LLMResponse（对齐本体流式响应对象字段，最后一条为完整结果）。
        插件 `async for resp in provider.text_chat_stream(...)` 至少能拿到完整回复。

        Args:
            prompt: 提示词，和 contexts 二选一使用
            system_prompt: 系统提示词
            image_urls: 图片 URL 列表
            audio_urls: 音频 URL 列表（宿主桥无对应通道，忽略）
            session_id: 会话 ID
            func_tool: 函数工具集合（宿主桥无对应通道，忽略）
            tool_choice: 工具调用策略（宿主桥无对应通道，忽略）
            contexts: 上下文，和 prompt 二选一使用
            request_max_retries: 最大重试次数（宿主桥无对应通道，忽略）
            **kwargs: 其他参数
        """
        prompt = prompt or ""
        # contexts 传入时宿主无对应通道，取最后一条文本兜底为 prompt
        if not prompt:
            contexts = contexts or kwargs.get("contexts") or []
            if contexts and isinstance(contexts[-1], dict) and contexts[-1].get("content"):
                prompt = str(contexts[-1]["content"])
        bridge = self._bridge()
        if bridge is None:
            raise RuntimeError("宿主桥未就绪（text_chat_stream 不可用）")
        text = await bridge.chat_llm_async(
            prompt,
            system_prompt or "",
            image_urls or [],
            session_id or "",
        )
        yield self._build_llm_response(text)

    async def test(self, timeout: float = 45.0) -> bool:
        """可达性测试（降级实现）。

        经宿主桥发 "REPLY PONG ONLY"（对齐本体语义），能拿到非空回复视为
        可达；超时或异常返回 False。插件（builtin_commands 的 provider
        列表测试）以 `await provider.test()` 的 True/False 判断可达性。
        """
        bridge = self._bridge()
        if bridge is None:
            return False
        try:
            text = await asyncio.wait_for(
                bridge.chat_llm_async("REPLY PONG ONLY", "", [], ""),
                timeout=timeout,
            )
            return bool(text)
        except Exception as e:
            logger.warning(f"provider.test({self.meta_id}) 失败: {e}")
            return False

    def get_current_key(self) -> str:
        return ""

    def get_keys(self) -> list[str]:
        keys = self.provider_config.get("key", [""])
        if isinstance(keys, str):
            keys = [keys]
        return list(keys) or [""]

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

    def support_stream(self) -> bool:
        """是否支持流式 TTS（降级：宿主无 TTS RPC，恒为 False）。"""
        return False

    async def get_audio(self, text: str) -> str:
        """获取文本的音频，返回音频文件路径（降级：宿主无 TTS RPC，返回 ""）。"""
        return ""


class STTProvider(Provider):
    """Speech-to-Text Provider（宿主 ListProviders 返回 speech_to_text 能力）。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        raw_pt = self.provider_config.get("provider_type")
        if not isinstance(raw_pt, str) or not raw_pt:
            self.provider_type = ProviderType.SPEECH_TO_TEXT

    async def get_text(self, audio_url: str) -> str:
        """获取音频的文本（降级：宿主无 STT RPC，返回 ""）。"""
        return ""


class EmbeddingProvider(Provider):
    """Embedding Provider（宿主 ListProviders 返回 embedding 能力）。

    OpenAI 兼容直调实现：宿主 ListProviders/GetUsingProvider 在 payload 中
    透传 ``key`` 与 ``api_base`` 时（provider_config），经 httpx 调用
    ``{api_base}/embeddings``。凭据缺失时保持降级语义（返回空/0），不影响
    不依赖 embedding 的插件。
    """

    # (api_base, model) → dim，进程级缓存避免重复探测。
    _dim_cache: dict[tuple[str, str], int] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        raw_pt = self.provider_config.get("provider_type")
        if not isinstance(raw_pt, str) or not raw_pt:
            self.provider_type = ProviderType.EMBEDDING

    def _credentials(self) -> tuple[str, str, str]:
        cfg = self.provider_config
        api_base = str(
            cfg.get("api_base") or cfg.get("base_url") or ""
        ).rstrip("/")
        key = str(cfg.get("key") or cfg.get("api_key") or "")
        model = self.model_name or str(cfg.get("model") or "")
        return api_base, key, model

    async def get_embedding(self, text: str) -> list[float]:
        """获取单个文本的向量。"""
        vecs = await self.get_embeddings([text])
        return vecs[0] if vecs else []

    async def get_embeddings(self, texts: list[str]) -> list[list[float]]:
        """批量获取文本向量（OpenAI 兼容 /embeddings，按 index 保序）。"""
        api_base, key, model = self._credentials()
        if not (api_base and key and model) or not texts:
            # 降级：宿主未透传凭据（旧版宿主），维持空返回语义。
            return []
        headers = {"Authorization": f"Bearer {key}"}
        payload = {"input": texts, "model": model}
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{api_base}/embeddings", json=payload, headers=headers
            )
            resp.raise_for_status()
            data = resp.json().get("data") or []
        data.sort(key=lambda d: d.get("index", 0))
        out = [list(d.get("embedding") or []) for d in data]
        if out and out[0]:
            self._dim_cache[(api_base, model)] = len(out[0])
        return out

    def get_dim(self) -> int:
        """获取向量维度；缓存未命中时同步探测一次短文本。

        FaissVecDB 构造期同步调用，探测用同步 httpx 一次性开销可接受。
        探测失败/无凭据时返回 0（降级语义，与旧版一致）。
        """
        api_base, key, model = self._credentials()
        if not (api_base and key and model):
            return 0
        cached = self._dim_cache.get((api_base, model))
        if cached:
            return cached
        dim = int(self.provider_config.get("embed_dim") or 0)
        if dim:
            self._dim_cache[(api_base, model)] = dim
            return dim
        try:
            resp = httpx.post(
                f"{api_base}/embeddings",
                json={"input": "dim", "model": model},
                headers={"Authorization": f"Bearer {key}"},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json().get("data") or []
            if data and data[0].get("embedding"):
                dim = len(data[0]["embedding"])
                self._dim_cache[(api_base, model)] = dim
        except Exception as e:  # noqa: BLE001 — 探测失败降级 0
            logger.warning(f"EmbeddingProvider.get_dim 探测失败: {e}")
        return dim


class RerankProvider(Provider):
    """Rerank Provider（宿主 ListProviders 返回 rerank 能力）。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        raw_pt = self.provider_config.get("provider_type")
        if not isinstance(raw_pt, str) or not raw_pt:
            self.provider_type = ProviderType.RERANK


Providers = (Provider, TTSProvider, STTProvider, EmbeddingProvider, RerankProvider)

# 模块级注册表：provider type → ProviderMetaData（对齐原版 register.py 的
# provider_cls_map 语义）。SDK 无第三方适配器，这里注册 SDK 内置的五个
# 能力类（'chat_completion'→Provider 等），供 meta() 等按类型名查询。
provider_cls_map: dict[str, ProviderMetaData] = {}

for _cap, _cls in (
    ("chat_completion", Provider),
    ("text_to_speech", TTSProvider),
    ("speech_to_text", STTProvider),
    ("embedding", EmbeddingProvider),
    ("rerank", RerankProvider),
):
    provider_cls_map[_cap] = ProviderMetaData(
        id="default",
        model=None,
        type=_cap,
        desc="SDK 内置能力类",
        cls_type=_cls,
        provider_type=_PROVIDER_TYPE_MAP[_cap],
        default_config_tmpl=None,
        provider_display_name=None,
    )
