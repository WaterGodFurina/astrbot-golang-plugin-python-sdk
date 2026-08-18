"""Provider 实体（Go 宿主兼容运行时）。"""
from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from typing import Any

from astrbot import logger
from astrbot.core.agent.message import (
    AssistantMessageSegment,
    ContentPart,
    ImageURLPart,
    TextPart,
    ToolCallMessageSegment,
    is_checkpoint_message,
)
from astrbot.core.utils.deprecation import deprecated


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
class ToolCallsResult:
    """工具调用结果（对齐 Python 本体 entities.py:65-87 的 ToolCallsResult）。

    携带一次工具调用轮次中 assistant 的工具调用请求（tool_calls_info）以及
    各工具的调用结果（tool_calls_result）。另提供扁平的 tool_call_name /
    tool_call_args / tool_call_id 便捷字段：当未传入消息段对象时，
    to_openai_messages() 直接据此构造 OpenAI 格式消息 dict。
    """

    tool_calls_info: AssistantMessageSegment | dict | None = None
    """函数调用请求消息（AssistantMessageSegment，含 tool_calls）"""
    tool_calls_result: list[ToolCallMessageSegment | dict] = field(default_factory=list)
    """函数调用结果消息列表（list[ToolCallMessageSegment]）"""
    tool_call_name: list[str] = field(default_factory=list)
    """函数调用名称列表"""
    tool_call_args: list[dict[str, Any]] = field(default_factory=list)
    """函数调用参数列表"""
    tool_call_id: list[str] = field(default_factory=list)
    """函数调用 ID 列表"""

    def to_openai_messages(self) -> list[dict]:
        """生成 OpenAI 格式的消息 dict 列表。

        若提供 tool_calls_info / tool_calls_result 消息段对象，直接序列化；
        否则用 tool_call_name / tool_call_args / tool_call_id 构造
        assistant tool_calls 消息与 tool 结果消息。
        """
        ret: list[dict] = []
        if self.tool_calls_info is not None:
            info = self.tool_calls_info
            ret.append(info.to_dict() if hasattr(info, "to_dict") else dict(info))
            for item in self.tool_calls_result:
                if hasattr(item, "to_dict"):
                    ret.append(item.to_dict())
                elif isinstance(item, dict):
                    ret.append(item)
                else:
                    ret.append({"role": "tool", "content": str(item)})
            return ret

        if self.tool_call_name or self.tool_call_id:
            calls = []
            count = max(len(self.tool_call_id), len(self.tool_call_name))
            for i in range(count):
                cid = self.tool_call_id[i] if i < len(self.tool_call_id) else ""
                name = self.tool_call_name[i] if i < len(self.tool_call_name) else ""
                args = self.tool_call_args[i] if i < len(self.tool_call_args) else {}
                calls.append(
                    {
                        "id": cid,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(args, ensure_ascii=False),
                        },
                    }
                )
            ret.append({"role": "assistant", "content": None, "tool_calls": calls})
            for item in self.tool_calls_result:
                if isinstance(item, dict):
                    ret.append(item)
                else:
                    ret.append({"role": "tool", "content": str(item)})
        return ret


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


@dataclass
class RerankResult:
    """重排结果（对齐 Python 本体 entities.py 的 RerankResult）。

    字段与原版一致：index 为在候选列表中的索引位置，relevance_score 为
    相关性分数。另补 to_dict() 方便插件直接转 dict 输出。
    """

    index: int = 0
    """在候选列表中的索引位置"""
    relevance_score: float = 0.0
    """相关性分数"""

    def to_dict(self) -> dict:
        """转为普通 dict。"""
        return {
            "index": self.index,
            "relevance_score": self.relevance_score,
        }


class AnthropicMessage:
    """Anthropic 响应消息（简化占位：dict 包装）。

    原版为 `anthropic.types.Message`（pydantic 模型），SDK 不依赖
    anthropic 库，这里用普通 dict 包装同一份数据，保留 to_dict() 接口。
    """

    def __init__(self, data: dict | None = None, **kwargs: Any) -> None:
        if data is None:
            data = kwargs
        self._data: dict = dict(data or {})

    def to_dict(self) -> dict:
        """转为普通 dict。"""
        return dict(self._data)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __repr__(self) -> str:
        return f"AnthropicMessage({self._data!r})"


class ChatCompletion:
    """OpenAI ChatCompletion 响应（简化占位类，含 to_dict）。

    原版为 `openai.types.chat.chat_completion.ChatCompletion`，SDK 不依赖
    openai 库，这里用普通 dict 包装同一份数据。
    """

    def __init__(self, data: dict | None = None, **kwargs: Any) -> None:
        if data is None:
            data = kwargs
        self._data: dict = dict(data or {})

    def to_dict(self) -> dict:
        """转为普通 dict。"""
        return dict(self._data)

    def model_dump(self) -> dict:
        """兼容原版 pydantic 的 model_dump 命名。"""
        return self.to_dict()

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __repr__(self) -> str:
        return f"ChatCompletion({self._data!r})"


class GenerateContentResponse:
    """Google GenAI GenerateContentResponse 响应（简化占位类，含 to_dict）。

    原版为 `google.genai.types.GenerateContentResponse`，SDK 不依赖
    google-genai 库，这里用普通 dict 包装同一份数据。
    """

    def __init__(self, data: dict | None = None, **kwargs: Any) -> None:
        if data is None:
            data = kwargs
        self._data: dict = dict(data or {})

    def to_dict(self) -> dict:
        """转为普通 dict。"""
        return dict(self._data)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __repr__(self) -> str:
        return f"GenerateContentResponse({self._data!r})"


class Response:
    """OpenAI Responses 响应（简化占位类，含 to_dict）。

    原版为 `openai.types.responses.Response`，SDK 不依赖 openai 库，
    这里用普通 dict 包装同一份数据。
    """

    def __init__(self, data: dict | None = None, **kwargs: Any) -> None:
        if data is None:
            data = kwargs
        self._data: dict = dict(data or {})

    def to_dict(self) -> dict:
        """转为普通 dict。"""
        return dict(self._data)

    def model_dump(self) -> dict:
        """兼容原版 pydantic 的 model_dump 命名。"""
        return self.to_dict()

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __repr__(self) -> str:
        return f"Response({self._data!r})"


@dataclass
class Conversation:
    """LLM 对话类（对齐 Python 本体 po.py 的 Conversation 字段）。

    原版为 SQLModel 表（po.py:558），SDK 不需要 pydantic / 数据库，用
    普通 dataclass 保存同一组字段。history 为字符串格式的对话列表
    （JSON 序列化的 OpenAI 格式消息 dict 列表）。
    """

    platform_id: str = ""
    """平台 ID"""
    user_id: str = ""
    """用户 ID"""
    cid: str = ""
    """对话 ID（uuid 格式字符串）"""
    history: str = ""
    """字符串格式的对话列表（JSON 序列化）"""
    title: str | None = ""
    """对话标题"""
    persona_id: str | None = ""
    """人格 ID"""
    created_at: int = 0
    """创建时间戳"""
    updated_at: int = 0
    """更新时间戳"""
    token_usage: int = 0
    """对话的总 token 数量"""

    def __iter__(self):
        """迭代 history 中解析出的消息 dict（history 为空时为空迭代）。"""
        if not self.history:
            return
        try:
            parsed = json.loads(self.history)
        except (TypeError, ValueError):
            return
        if isinstance(parsed, list):
            yield from parsed

    @classmethod
    def from_messages(cls, messages: list[dict]) -> "Conversation":
        """从 OpenAI 格式消息 dict 列表构造对话（history 为 JSON 字符串）。"""
        return cls(history=json.dumps(messages, ensure_ascii=False))

    def to_dict(self) -> dict:
        """转为普通 dict。"""
        return {
            "platform_id": self.platform_id,
            "user_id": self.user_id,
            "cid": self.cid,
            "history": self.history,
            "title": self.title,
            "persona_id": self.persona_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "token_usage": self.token_usage,
        }


class MediaResolver:
    """媒体解析器（简化占位：解析 url/path/base64 图片到可发送形式）。

    原版为 `astrbot.core.utils.media_utils.MediaResolver`（真正做下载 /
    base64 编码 / 压缩），SDK 不引入图片处理依赖，这里仅解析输入引用
    并原样返回 dict（含 url/type），供插件调用不报错。
    """

    def __init__(
        self,
        media_ref: str = "",
        media_type: str = "image",
        default_suffix: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.media_ref: str = media_ref or ""
        self.media_type: str = media_type or "image"
        self.default_suffix: str | None = default_suffix
        self.kwargs: dict = kwargs or {}

    def resolve(self, **kwargs: Any) -> dict:
        """解析媒体引用为可发送形式的 dict（简化：原样返回）。"""
        return {
            "url": self.media_ref,
            "type": self.media_type,
            "media_type": self.media_type,
            **(self.kwargs or {}),
            **(kwargs or {}),
        }

    async def to_base64_data(self, strict: bool = False, target_format: str | None = None) -> dict:
        """异步转为 base64 数据（简化：不真正编码，返回 resolve() 的 dict）。"""
        return self.resolve(strict=strict, target_format=target_format)

    def to_data_url(self) -> str:
        """转为 data URL（简化：对 raw base64 输入做轻量包装，否则原样返回）。"""
        ref = self.media_ref
        if ref.startswith("data:"):
            return ref
        if ref.startswith("base64://"):
            payload = ref[len("base64://"):]
            return f"data:{self.media_type};base64,{payload}"
        return ref
