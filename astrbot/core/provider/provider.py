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
from typing import Any, TypeAlias, Union

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
        session_id: str | None = None,
        image_urls: list[str] | None = None,
        audio_urls: list[str] | None = None,
        func_tool: Any | None = None,
        contexts: list | None = None,
        system_prompt: str | None = None,
        tool_calls_result: Any | None = None,
        model: str | None = None,
        extra_user_content_parts: list | None = None,
        tool_choice: Any | None = "auto",
        request_max_retries: int | None = None,
        **kwargs: Any,
    ) -> Any:
        """非流式 LLM 调用（降级实现，签名对齐本体 text_chat）。

        Go 宿主桥无非流式专用 RPC：复用 chat_llm_async 拿完整文本后，
        包装为 `LLMResponse(role="assistant") + result_chain=[Plain(text)]`
        （对齐本体 text_chat 的返回对象）。

        Args:
            prompt: 提示词，和 contexts 二选一使用
            session_id: 会话 ID（宿主桥透传，语义由宿主决定；本体已废弃此参数）
            image_urls: 图片 URL 列表
            audio_urls: 音频 URL 列表（宿主桥 chat_llm_async 已透传，
                不支持时由宿主侧忽略）
            func_tool: 函数工具集合（宿主桥无对应通道，忽略）
            contexts: 上下文，和 prompt 二选一使用（存在时取末条文本拼到
                prompt 前，参考 Context.llm_generate 的现有做法）
            system_prompt: 系统提示词
            tool_calls_result: 回传给 LLM 的工具调用结果（宿主桥无对应
                通道，忽略）
            model: 模型名（宿主桥无对应通道，忽略；使用宿主当前配置模型）
            extra_user_content_parts: 额外内容块列表（宿主桥无对应通道，忽略）
            tool_choice: 工具调用策略（宿主桥无对应通道，忽略）
            request_max_retries: 最大重试次数（宿主桥无对应通道，忽略）
            **kwargs: 其他参数
        """
        prompt = prompt or ""
        # contexts 传入时宿主无对应通道，取最后一条文本拼到 prompt 前
        contexts = contexts or kwargs.get("contexts") or []
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
        session_id: str | None = None,
        image_urls: list[str] | None = None,
        audio_urls: list[str] | None = None,
        func_tool: Any | None = None,
        contexts: list | None = None,
        system_prompt: str | None = None,
        tool_calls_result: Any | None = None,
        model: str | None = None,
        tool_choice: Any | None = "auto",
        request_max_retries: int | None = None,
        **kwargs: Any,
    ) -> Any:
        """流式 LLM 调用（降级实现，签名对齐本体 text_chat_stream）。

        Go 宿主桥无流式 RPC：经 chat_llm_async 拿完整文本后，yield 一个
        LLMResponse（对齐本体流式响应对象字段，最后一条为完整结果）。
        插件 `async for resp in provider.text_chat_stream(...)` 至少能拿到完整回复。

        Args:
            prompt: 提示词，和 contexts 二选一使用
            session_id: 会话 ID（宿主桥透传，语义由宿主决定）
            image_urls: 图片 URL 列表
            audio_urls: 音频 URL 列表（宿主桥无对应通道，忽略）
            func_tool: 函数工具集合（宿主桥无对应通道，忽略）
            contexts: 上下文，和 prompt 二选一使用（存在时取末条文本兜底为 prompt）
            system_prompt: 系统提示词
            tool_calls_result: 回传给 LLM 的工具调用结果（宿主桥无对应通道，忽略）
            model: 模型名（宿主桥无对应通道，忽略）
            tool_choice: 工具调用策略（宿主桥无对应通道，忽略）
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

    async def pop_record(self, context: list) -> None:
        """弹出 context 第一条非系统提示词对话记录（对齐本体语义）。"""
        poped = 0
        indexs_to_pop = []
        for idx, record in enumerate(context):
            if record["role"] == "system":
                continue
            indexs_to_pop.append(idx)
            poped += 1
            if poped == 2:
                break

        for idx in reversed(indexs_to_pop):
            context.pop(idx)

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

    async def get_audio_stream(
        self,
        text_queue: asyncio.Queue,
        audio_queue: asyncio.Queue,
    ) -> None:
        """流式 TTS 处理方法（对齐本体默认实现）。

        从 text_queue 中读取文本片段并累积；收到 None（输入结束）时把
        累积文本一次性交给 get_audio 生成音频文件，读取文件内容后以
        `(text, bytes)` 形式放入 audio_queue，最后放入 None 表示输出结束。
        生成失败时跳过音频数据，但仍发送 None 结束标记（对齐本体）。
        """
        accumulated_text = ""

        while True:
            text_part = await text_queue.get()

            if text_part is None:
                # 输入结束，处理累积的文本
                if accumulated_text:
                    try:
                        audio_path = await self.get_audio(accumulated_text)
                        with open(audio_path, "rb") as f:
                            audio_data = f.read()
                        await audio_queue.put((accumulated_text, audio_data))
                    except Exception:
                        # 出错时也要发送 None 结束标记
                        pass
                await audio_queue.put(None)
                break

            accumulated_text += text_part

    async def test(self) -> bool:
        """可达性测试（降级：调用 get_audio("hi")，路径为空/文件缺失视为不可达）。"""
        try:
            audio_path = await self.get_audio("hi")
            if not audio_path or not os.path.exists(audio_path):
                return False
            return os.path.getsize(audio_path) > 0
        except Exception:
            return False


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

    async def test(self) -> bool:
        """可达性测试（降级：调用 get_text，返回空文本视为不可达）。"""
        try:
            return bool(await self.get_text(""))
        except Exception:
            return False


class EmbeddingProvider(Provider):
    """Embedding Provider（宿主 ListProviders 返回 embedding 能力）。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        raw_pt = self.provider_config.get("provider_type")
        if not isinstance(raw_pt, str) or not raw_pt:
            self.provider_type = ProviderType.EMBEDDING

    async def get_embedding(self, text: str) -> list[float]:
        """获取单个文本的向量（降级：宿主无 Embedding RPC，返回 []）。"""
        return []

    async def get_embeddings(self, text: list[str]) -> list[list[float]]:
        """批量获取文本的向量（降级：宿主无 Embedding RPC，返回 []）。

        Args:
            text: 文本列表（参数名对齐本体 ``get_embeddings(text: list[str])``，
                插件按名传参 ``text=`` 不会 TypeError）
        """
        return []

    async def get_embeddings_batch(
        self,
        texts: list[str],
        batch_size: int = 16,
        tasks_limit: int = 3,
        max_retries: int = 3,
        progress_callback=None,
    ) -> list[list[float]]:
        """批量获取文本的向量，分批处理以节省内存（对齐原版语义）。

        原版 astrbot-py 的 EmbeddingProvider 基类提供该方法（分片 + 信号量
        并发 + 指数退避重试 + 进度回调），FaissVecDB.add_documents 经
        ``self.embedding_provider.get_embeddings_batch(...)`` 调用；SDK 基类
        缺失该方法时，livingmemory 反思入库（graph_vector_retriever →
        faiss insert_batch）直接 AttributeError。默认实现按原版语义包装
        ``self.get_embeddings``，子类已实现批量端点的可直接复用。

        Args:
            texts: 文本列表（参数名对齐本体 get_embeddings_batch）
            batch_size: 每批处理的文本数量
            tasks_limit: 并发任务数量限制
            max_retries: 失败时的最大重试次数
            progress_callback: 进度回调函数，接收参数 (current, total)

        Returns:
            向量列表
        """
        semaphore = asyncio.Semaphore(tasks_limit)
        batch_results: dict[int, list[list[float]]] = {}
        completed_count = 0
        total_count = len(texts)

        async def process_batch(batch_idx: int, batch_texts: list[str]) -> None:
            nonlocal completed_count
            async with semaphore:
                for attempt in range(max_retries):
                    try:
                        batch_embeddings = await self.get_embeddings(batch_texts)
                        batch_results[batch_idx] = batch_embeddings
                        completed_count += len(batch_texts)
                        if progress_callback:
                            res = progress_callback(completed_count, total_count)
                            if asyncio.iscoroutine(res):
                                await res
                        return
                    except Exception:
                        if attempt == max_retries - 1:
                            raise
                        # 指数退避后重试（对齐原版）
                        await asyncio.sleep(2**attempt)

        tasks = []
        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i : i + batch_size]
            batch_idx = i // batch_size
            tasks.append(process_batch(batch_idx, batch_texts))

        await asyncio.gather(*tasks)

        all_embeddings: list[list[float]] = []
        for batch_idx in range(len(tasks)):
            all_embeddings.extend(batch_results[batch_idx])
        return all_embeddings

    def get_dim(self) -> int:
        """获取向量的维度（降级：宿主无 Embedding RPC，返回 0）。"""
        return 0

    async def test(self) -> bool:
        """可达性测试（降级：调用 get_embedding("astrbot")，空向量视为不可达）。"""
        try:
            return bool(await self.get_embedding("astrbot"))
        except Exception:
            return False


class RerankProvider(Provider):
    """Rerank Provider（宿主 ListProviders 返回 rerank 能力）。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        raw_pt = self.provider_config.get("provider_type")
        if not isinstance(raw_pt, str) or not raw_pt:
            self.provider_type = ProviderType.RERANK

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int | None = None,
    ) -> list:
        """获取查询和文档的重排序分数（签名对齐本体 RerankProvider.rerank）。

        降级说明：Go 宿主桥（HostService proto）未暴露 rerank RPC（宿主
        Go 侧有 TEIRerankSource.Rerank，见 internal/provider/sources/
        rerank_source.go，但未接入 Python 沙箱通道），SDK 基类返回空列表
        表示无重排结果。插件应自行处理空结果（对齐本体行为：无可用
        Rerank 服务时 test() 抛异常，此处以空列表降级不炸）。

        Args:
            query: 查询文本
            documents: 候选文档列表
            top_n: 返回的最大结果数（None 表示全部）

        Returns:
            list[RerankResult]（降级：恒为空列表）
        """
        return []

    async def test(self) -> bool:
        """可达性测试（降级：调用 rerank，无结果视为不可达）。"""
        try:
            return bool(await self.rerank("Apple", documents=["apple", "banana"]))
        except Exception:
            return False


Providers: TypeAlias = Union[
    "Provider",
    "STTProvider",
    "TTSProvider",
    "EmbeddingProvider",
    "RerankProvider",
]

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
