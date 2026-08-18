"""Provider 实体（Go 宿主兼容运行时）。"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class ProviderType(enum.Enum):
    """提供商能力类型（对齐 Python 本体 entities.py 的 ProviderType 枚举）。"""

    CHAT_COMPLETION = "chat_completion"
    SPEECH_TO_TEXT = "speech_to_text"
    TEXT_TO_SPEECH = "text_to_speech"
    EMBEDDING = "embedding"
    RERANK = "rerank"


@dataclass
class ProviderMeta:
    """提供商实例的元数据（对齐 Python 本体 entities.py 的 ProviderMeta）。"""

    id: str
    """the unique id of the provider instance that user configured"""
    model: str | None
    """the model name of the provider instance currently used"""
    type: str
    """the name of the provider adapter, such as openai, ollama"""
    provider_type: ProviderType = ProviderType.CHAT_COMPLETION
    """the capability type of the provider adapter"""


@dataclass
class ProviderMetaData(ProviderMeta):
    """提供商适配器注册元数据（占位：Go 宿主无适配器注册体系）。"""

    desc: str = ""
    cls_type: Any | None = None
    default_config_tmpl: dict | None = None
    provider_display_name: str | None = None


@dataclass
class ProviderRequest:
    prompt: str | None = None
    session_id: str | None = ""
    image_urls: list[str] = field(default_factory=list)
    audio_urls: list[str] = field(default_factory=list)
    extra_user_content_parts: list = field(default_factory=list)
    func_tool: Any | None = None
    contexts: list[dict] = field(default_factory=list)
    system_prompt: str = ""
    conversation: Any | None = None
    tool_calls_result: Any | None = None
    model: str | None = None


@dataclass
class TokenUsage:
    """Token 用量（对齐 Python 本体 entities.py 的 TokenUsage）。"""

    input_other: int = 0
    """非缓存的输入 token 数"""
    input_cached: int = 0
    """缓存的输入 token 数"""
    output: int = 0
    """输出 token 数"""

    @property
    def total(self) -> int:
        return self.input_other + self.input_cached + self.output

    @property
    def input(self) -> int:
        return self.input_other + self.input_cached

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_other=self.input_other + other.input_other,
            input_cached=self.input_cached + other.input_cached,
            output=self.output + other.output,
        )

    def __sub__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_other=self.input_other - other.input_other,
            input_cached=self.input_cached - other.input_cached,
            output=self.output - other.output,
        )


@dataclass
class LLMResponse:
    """LLM 响应（对齐 Python 本体 entities.py 的 LLMResponse 字段全集）。

    completion_text 为可写属性（构造参数 + 赋值均支持）：写入时若有
    result_chain 则同步改写其 Plain 组件，否则写 _completion_text。
    """

    role: str = "assistant"
    """角色：assistant / tool / err"""
    result_chain: Any | None = None
    """LLM 文本完成结果的消息链"""
    tools_call_args: list[dict[str, Any]] = field(default_factory=list)
    """工具调用参数"""
    tools_call_name: list[str] = field(default_factory=list)
    """工具调用名称"""
    tools_call_ids: list[str] = field(default_factory=list)
    """工具调用 ID"""
    tools_call_extra_content: dict[str, dict[str, Any]] = field(default_factory=dict)
    """工具调用附加内容（tool_call_id -> extra_content dict）"""
    reasoning_content: str | None = None
    """推理内容（如有）"""
    reasoning_signature: str | None = None
    """推理内容的签名（如有）"""
    raw_completion: Any | None = None
    """LLM 提供商的原始响应对象"""
    _completion_text: str = ""
    """完成的纯文本（result_chain 为空时兜底）"""
    is_chunk: bool = False
    """是否为流式分块响应"""
    id: str | None = None
    """响应 ID"""
    usage: TokenUsage | None = None
    """Token 用量（如有）"""

    def __init__(
        self,
        role: str = "assistant",
        completion_text: str | None = None,
        result_chain: Any | None = None,
        tools_call_args: list[dict[str, Any]] | None = None,
        tools_call_name: list[str] | None = None,
        tools_call_ids: list[str] | None = None,
        tools_call_extra_content: dict[str, dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
        reasoning_signature: str | None = None,
        raw_completion: Any | None = None,
        is_chunk: bool = False,
        id: str | None = None,
        usage: TokenUsage | None = None,
    ) -> None:
        """初始化 LLMResponse（对齐本体的构造参数全集）。

        Args:
            role: 角色，assistant / tool / err
            completion_text: 返回的结果文本（可省略，用 result_chain 代替）
            result_chain: 返回的消息链
            usage: Token 用量
        """
        if tools_call_args is None:
            tools_call_args = []
        if tools_call_name is None:
            tools_call_name = []
        if tools_call_ids is None:
            tools_call_ids = []
        if tools_call_extra_content is None:
            tools_call_extra_content = {}

        self.role = role
        self.completion_text = completion_text
        self.result_chain = result_chain
        self.tools_call_args = tools_call_args
        self.tools_call_name = tools_call_name
        self.tools_call_ids = tools_call_ids
        self.tools_call_extra_content = tools_call_extra_content
        self.reasoning_content = reasoning_content
        self.reasoning_signature = reasoning_signature
        self.raw_completion = raw_completion
        self.is_chunk = is_chunk

        if id is not None:
            self.id = id
        if usage is not None:
            self.usage = usage

    @property
    def completion_text(self) -> str:
        """完成的纯文本（可读可写）。"""
        if self.result_chain:
            return self.result_chain.get_plain_text()
        return self._completion_text

    @completion_text.setter
    def completion_text(self, value) -> None:
        """写入纯文本：有 result_chain 时改写其 Plain 组件，否则写 _completion_text。"""
        if self.result_chain:
            from astrbot.core.message.components import Plain

            self.result_chain.chain = [
                comp
                for comp in self.result_chain.chain
                if not isinstance(comp, Plain)
            ]  # 清空 Plain 组件
            if value is not None:
                self.result_chain.chain.insert(0, Plain(value))
        else:
            self._completion_text = value

    def __str__(self) -> str:
        return self.completion_text if self.completion_text is not None else ""


@dataclass
class ToolCall:
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_id: str = ""


@dataclass
class PluginError:
    handler_name: str = ""
    error: str = ""
