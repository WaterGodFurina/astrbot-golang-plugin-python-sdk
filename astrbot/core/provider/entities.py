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
class LLMResponse:
    role: str = "assistant"
    result_chain: Any | None = None
    tools_call_args: list[dict[str, Any]] = field(default_factory=list)
    tools_call_name: list[str] = field(default_factory=list)
    tools_call_ids: list[str] = field(default_factory=list)
    tools_call_extra_content: dict = field(default_factory=dict)
    reasoning_content: str | None = None
    reasoning_signature: str | None = None
    raw_completion: Any | None = None
    _completion_text: str = ""

    @property
    def completion_text(self) -> str:
        if self.result_chain is None:
            return self._completion_text
        return self.result_chain.get_plain_text()

    def __str__(self) -> str:
        return self.completion_text


@dataclass
class ToolCall:
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_id: str = ""


@dataclass
class PluginError:
    handler_name: str = ""
    error: str = ""
