"""Agent 消息类型（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.agent.message` 的核心类型语义，但不依赖
pydantic，用普通 dataclass / 纯 Python 类实现：

- `ContentPart` 内容部件层级（text / think / image_url / audio_url），
  支持 `to_dict()` / `from_dict()` 与 OpenAI 格式 dict 互转；
- `ToolCall` 工具调用（id/function{name, arguments} 结构，对齐原版
  pydantic 定义；与 `astrbot.core.provider.entities.ToolCall` 的扁平
  tool_name/tool_args/tool_id 结构不同，两者用途不同，均保留）；
- `Message` 会话消息 + 各角色 segment 子类（User/Assistant/ToolCall/
  System/Checkpoint）。

`Message.to_dict()` / `Message.from_dict()` 对齐原版 model_dump /
model_validate 的语义（简化版）：内容部件与工具调用递归转成普通 dict，
可选字段（tool_calls / tool_call_id / name / extra）为 None 时省略。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, TypeVar

# 内容部件类型常量（同时是 ContentPart 注册表的键）
TYPE_TEXT = "text"
TYPE_THINK = "think"
TYPE_IMAGE_URL = "image_url"
TYPE_AUDIO_URL = "audio_url"

# 内部 checkpoint 消息的角色名（用于把 LLM 轮次与平台历史关联）
CHECKPOINT_ROLE = "_checkpoint"


class ContentPart:
    """消息内容的一个部件（抽象基类，支持 dict 转换）。

    子类必须声明字符串 `type` 类属性（作为注册键，构造时自动登记到
    全局注册表）。`to_dict()` 输出与原版 model_dump() 一致的 dict；
    `from_dict()` 依据 dict["type"] 分发到对应子类解析。
    """

    __content_part_registry: ClassVar[dict[str, type["ContentPart"]]] = {}
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        type_value = getattr(cls, "type", None)
        if not isinstance(type_value, str) or not type_value:
            raise ValueError(
                f"ContentPart 子类 {cls.__name__} 必须声明字符串 type 字段"
            )
        cls.__content_part_registry[type_value] = cls

    def to_dict(self) -> dict:
        """转为普通 dict（子类必须实现）。"""
        raise NotImplementedError

    @classmethod
    def from_dict(cls, data: Any) -> "ContentPart":
        """从 dict（或已实例化的 ContentPart）解析出对应子类实例。"""
        if isinstance(data, cls):
            return data
        if not isinstance(data, dict) or not isinstance(data.get("type"), str):
            raise ValueError(f"无法将 {data!r} 解析为 ContentPart")
        target = cls.__content_part_registry.get(data["type"])
        if target is None:
            raise ValueError(f"未知的 ContentPart 类型: {data['type']}")
        return target._from_dict(data)


# typing 层 TypeVar：标注某个内容部件子类自身（如 mark_as_temp 返回类型），
# 对齐本体 `ContentPartT = TypeVar("ContentPartT", bound="ContentPart")`。
ContentPartT = TypeVar("ContentPartT", bound=ContentPart)


class TextPart(ContentPart):
    """文本内容部件。

    >>> TextPart("你好").to_dict()
    {'type': 'text', 'text': '你好'}
    """

    type = TYPE_TEXT

    def __init__(self, content: str = "", *, text: str | None = None):
        self.type = TYPE_TEXT
        # 兼容 TextPart("...") 与 TextPart(text="...") 两种构造方式
        self.text = text if text is not None else content

    def to_dict(self) -> dict:
        return {"type": self.type, "text": self.text}

    @classmethod
    def _from_dict(cls, data: dict) -> "TextPart":
        return cls(text=data.get("text", ""))

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, TextPart) and other.text == self.text

    def __repr__(self) -> str:
        return f"TextPart(text={self.text!r})"


class ThinkPart(ContentPart):
    """推理内容部件（encrypted 为加密推理内容或签名）。

    >>> ThinkPart("让我想想").to_dict()
    {'type': 'think', 'think': '让我想想', 'encrypted': None}
    """

    type = TYPE_THINK

    def __init__(
        self,
        content: str = "",
        *,
        think: str | None = None,
        encrypted: str | None = None,
    ):
        self.type = TYPE_THINK
        # 兼容 ThinkPart("...") 与 ThinkPart(think="...") 两种构造方式
        self.think = think if think is not None else content
        self.encrypted = encrypted

    def to_dict(self) -> dict:
        return {"type": self.type, "think": self.think, "encrypted": self.encrypted}

    @classmethod
    def _from_dict(cls, data: dict) -> "ThinkPart":
        return cls(think=data.get("think", ""), encrypted=data.get("encrypted"))

    def merge_in_place(self, other: Any) -> bool:
        """把另一个 ThinkPart 追加合并到当前实例（流式累积推理内容）。"""
        if not isinstance(other, ThinkPart):
            return False
        if self.encrypted:
            return False
        self.think += other.think
        if other.encrypted:
            self.encrypted = other.encrypted
        return True

    def __eq__(self, other: Any) -> bool:
        return (
            isinstance(other, ThinkPart)
            and other.think == self.think
            and other.encrypted == self.encrypted
        )

    def __repr__(self) -> str:
        return f"ThinkPart(think={self.think!r})"


class ImageURLPart(ContentPart):
    """图片 URL 内容部件。

    image_url 形如 {"url": "...", "id": None}，与 OpenAI 图片消息结构一致。

    >>> ImageURLPart("http://example.com/a.png").to_dict()
    {'type': 'image_url', 'image_url': {'url': 'http://example.com/a.png', 'id': None}}
    """

    type = TYPE_IMAGE_URL

    def __init__(
        self,
        url: str = "",
        *,
        id: str | None = None,
        image_url: dict | None = None,
    ):
        self.type = TYPE_IMAGE_URL
        if image_url is not None:
            self.image_url = image_url
        else:
            d: dict = {"url": url}
            if id is not None:
                d["id"] = id
            self.image_url = d

    @property
    def url(self) -> str:
        return self.image_url.get("url", "")

    def to_dict(self) -> dict:
        return {"type": self.type, "image_url": self.image_url}

    @classmethod
    def _from_dict(cls, data: dict) -> "ImageURLPart":
        return cls(image_url=data.get("image_url", {}))

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, ImageURLPart) and other.image_url == self.image_url

    def __repr__(self) -> str:
        return f"ImageURLPart(image_url={self.image_url!r})"


class AudioURLPart(ContentPart):
    """音频 URL 内容部件。

    audio_url 形如 {"url": "...", "id": None}，与 OpenAI 音频消息结构一致。

    >>> AudioURLPart("http://example.com/a.mp3").to_dict()
    {'type': 'audio_url', 'audio_url': {'url': 'http://example.com/a.mp3', 'id': None}}
    """

    type = TYPE_AUDIO_URL

    def __init__(
        self,
        url: str = "",
        *,
        id: str | None = None,
        audio_url: dict | None = None,
    ):
        self.type = TYPE_AUDIO_URL
        if audio_url is not None:
            self.audio_url = audio_url
        else:
            d: dict = {"url": url}
            if id is not None:
                d["id"] = id
            self.audio_url = d

    @property
    def url(self) -> str:
        return self.audio_url.get("url", "")

    def to_dict(self) -> dict:
        return {"type": self.type, "audio_url": self.audio_url}

    @classmethod
    def _from_dict(cls, data: dict) -> "AudioURLPart":
        return cls(audio_url=data.get("audio_url", {}))

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, AudioURLPart) and other.audio_url == self.audio_url

    def __repr__(self) -> str:
        return f"AudioURLPart(audio_url={self.audio_url!r})"


class ToolCall:
    """工具调用（对齐原版 agent/message.py 的 ToolCall：id/function 结构）。

    - `function` 为属性，返回 {"name": ..., "arguments": ...}；
    - `to_dict()` 输出 OpenAI 格式：{"type": "function", "id": ..., "function": ...}；
    - 与 `astrbot.core.provider.entities.ToolCall`（tool_name/tool_args/tool_id
      扁平结构）不同，两者用途不同，均保留。
    """

    def __init__(
        self,
        id: str = "",
        name: str = "",
        arguments: str | None = None,
        extra_content: dict | None = None,
    ):
        self.id = id
        self.name = name
        self.arguments = arguments
        self.extra_content = extra_content

    @property
    def function(self) -> dict:
        """函数体：{"name": ..., "arguments": ...}。"""
        return {"name": self.name, "arguments": self.arguments}

    def to_dict(self) -> dict:
        data: dict = {
            "type": "function",
            "id": self.id,
            "function": {"name": self.name, "arguments": self.arguments},
        }
        if self.extra_content is not None:
            data["extra_content"] = self.extra_content
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "ToolCall":
        if not isinstance(data, dict):
            raise ValueError(f"无法将 {data!r} 解析为 ToolCall")
        function = data.get("function")
        if isinstance(function, dict):
            name = function.get("name", "")
            arguments = function.get("arguments")
        else:
            name, arguments = "", None
        return cls(
            id=data.get("id", ""),
            name=name,
            arguments=arguments,
            extra_content=data.get("extra_content"),
        )

    def __eq__(self, other: Any) -> bool:
        return (
            isinstance(other, ToolCall)
            and other.id == self.id
            and other.name == self.name
            and other.arguments == self.arguments
            and other.extra_content == self.extra_content
        )

    def __repr__(self) -> str:
        return f"ToolCall(id={self.id!r}, function={self.function!r})"


@dataclass
class ToolCallPart:
    """工具调用消息段（对齐原版 ToolCallPart 语义）。

    对应一次工具调用：标识（tool_call_id）、函数名（name）、参数
    （arguments，JSON 字符串）与执行结果（result），并带角色（role，
    默认 "tool"）。为普通 dataclass，含 `to_dict()` / `from_dict()`。
    """

    tool_call_id: str = ""
    """工具调用标识"""
    name: str = ""
    """函数名"""
    arguments: str = "{}"
    """函数参数，JSON 字符串"""
    result: str | None = None
    """工具执行结果（可空）"""
    role: str = "tool"
    """消息段角色（默认 tool）"""

    def to_dict(self) -> dict:
        """转为普通 dict（result 为 None 时省略，对齐序列化语义）。"""
        data: dict = {
            "tool_call_id": self.tool_call_id,
            "name": self.name,
            "arguments": self.arguments,
            "role": self.role,
        }
        if self.result is not None:
            data["result"] = self.result
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "ToolCallPart":
        """从 dict 解析出 ToolCallPart（缺省字段取默认值）。"""
        if not isinstance(data, dict):
            raise ValueError(f"无法将 {data!r} 解析为 ToolCallPart")
        return cls(
            tool_call_id=data.get("tool_call_id", ""),
            name=data.get("name", ""),
            arguments=data.get("arguments", "{}"),
            result=data.get("result"),
            role=data.get("role", "tool"),
        )

    def __eq__(self, other: Any) -> bool:
        return (
            isinstance(other, ToolCallPart)
            and all(
                getattr(self, fname) == getattr(other, fname)
                for fname in ("tool_call_id", "name", "arguments", "result", "role")
            )
        )

    def __repr__(self) -> str:
        return f"ToolCallPart(tool_call_id={self.tool_call_id!r}, name={self.name!r})"


@dataclass
class CheckpointData:
    """内部 checkpoint 数据：用于把 LLM 轮次与平台历史关联。"""

    id: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id}

    @classmethod
    def from_dict(cls, data: dict) -> "CheckpointData":
        return cls(id=data.get("id", "") if isinstance(data, dict) else "")


@dataclass
class Message:
    """一条会话消息。

    - role: system / user / assistant / tool / _checkpoint
    - content: 字符串、内容部件列表、CheckpointData 或 None（assistant 且
      携带 tool_calls 时允许为 None）
    - tool_calls: 助手请求的工具调用列表（可选，为 None 时序列化省略）
    - name / tool_call_id: 工具消息使用的可选字段（为 None 时序列化省略）
    - extra: 附加元数据（为 None 时序列化省略）
    """

    role: str = "user"
    content: str | list[ContentPart] | CheckpointData | None = None
    tool_calls: list[ToolCall] | list[dict] | None = None
    name: str | None = None
    tool_call_id: str | None = None
    extra: dict | None = None
    # 绑定的 checkpoint（bind_checkpoint_messages 写入；不参与构造/序列化）
    _checkpoint_after: CheckpointData | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def to_dict(self) -> dict:
        """转为 OpenAI 格式 dict（可选字段为 None 时省略，对齐原版语义）。"""
        data: dict = {"role": self.role}

        content = self.content
        if isinstance(content, ContentPart):
            data["content"] = content.to_dict()
        elif isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, ContentPart):
                    parts.append(part.to_dict())
                else:
                    parts.append(part)
            data["content"] = parts
        elif isinstance(content, CheckpointData):
            data["content"] = content.to_dict()
        else:
            data["content"] = content

        if self.tool_calls is not None:
            calls = []
            for call in self.tool_calls:
                if isinstance(call, ToolCall):
                    calls.append(call.to_dict())
                else:
                    calls.append(call)
            data["tool_calls"] = calls
        if self.tool_call_id is not None:
            data["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            data["name"] = self.name
        if self.extra is not None:
            data["extra"] = self.extra
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        """从 OpenAI 格式 dict 解析出 Message（对齐原版 model_validate 语义）。"""
        if not isinstance(data, dict):
            raise ValueError(f"无法将 {data!r} 解析为 Message")

        role = data.get("role", "user")
        content = data.get("content")
        if isinstance(content, list):
            content = [
                ContentPart.from_dict(c) if isinstance(c, dict) and "type" in c else c
                for c in content
            ]
        elif isinstance(content, dict):
            if "type" in content:
                content = ContentPart.from_dict(content)
            elif role == CHECKPOINT_ROLE:
                content = CheckpointData.from_dict(content)

        tool_calls = data.get("tool_calls")
        if isinstance(tool_calls, list):
            tool_calls = [
                ToolCall.from_dict(c) if isinstance(c, dict) else c for c in tool_calls
            ]

        return cls(
            role=role,
            content=content,
            tool_calls=tool_calls,
            name=data.get("name"),
            tool_call_id=data.get("tool_call_id"),
            extra=data.get("extra"),
        )


@dataclass
class UserMessageSegment(Message):
    """用户消息段。"""

    role: str = "user"


@dataclass
class AssistantMessageSegment(Message):
    """助手消息段。"""

    role: str = "assistant"


@dataclass
class ToolCallMessageSegment(Message):
    """工具调用结果消息段。"""

    role: str = "tool"


@dataclass
class SystemMessageSegment(Message):
    """系统消息段。"""

    role: str = "system"


@dataclass
class CheckpointMessageSegment(Message):
    """内部 checkpoint 消息段（用于持久化会话历史）。"""

    role: str = CHECKPOINT_ROLE
    content: Any = None


def is_checkpoint_message(message: "Message | dict") -> bool:
    """判断消息是否为内部 checkpoint。"""
    if isinstance(message, Message):
        return message.role == CHECKPOINT_ROLE
    return isinstance(message, dict) and message.get("role") == CHECKPOINT_ROLE


def get_checkpoint_id(message: "Message | dict") -> str | None:
    """从内部 checkpoint 消息中取出 checkpoint id。"""
    if not is_checkpoint_message(message):
        return None
    content = message.content if isinstance(message, Message) else message.get("content")
    if isinstance(content, CheckpointData):
        return content.id
    if isinstance(content, dict):
        checkpoint_id = content.get("id")
        return checkpoint_id if isinstance(checkpoint_id, str) and checkpoint_id else None
    return None


def strip_checkpoint_messages(history: list) -> list:
    """从（provider 侧）历史中移除内部 checkpoint 消息。"""
    return [message for message in history if not is_checkpoint_message(message)]


def _get_checkpoint_data(message: "Message | dict") -> CheckpointData | None:
    """解析 checkpoint 消息内容为 CheckpointData（简化版）。

    非 checkpoint 消息返回 None；内容已是 CheckpointData 直接返回；
    内容为 dict 时尝试按 {"id": ...} 解析，失败返回 None。
    """
    if not is_checkpoint_message(message):
        return None
    content = message.content if isinstance(message, Message) else message.get("content")
    if isinstance(content, CheckpointData):
        return content
    if isinstance(content, dict):
        try:
            return CheckpointData.from_dict(content)
        except Exception:
            return None
    return None


def bind_checkpoint_messages(history: list) -> list[Message]:
    """加载持久化历史，并把 checkpoint 绑定到其前一条消息上（简化版）。

    遍历 history：checkpoint 消息解析出 CheckpointData 后写入前一条消息的
    `_checkpoint_after`（不做额外校验）；其余条目用 `Message.from_dict`
    解析后追加。返回绑定后的消息列表。
    """
    messages: list[Message] = []
    for item in history:
        if is_checkpoint_message(item):
            checkpoint = _get_checkpoint_data(item)
            if checkpoint is not None and messages:
                messages[-1]._checkpoint_after = checkpoint
            continue
        message = Message.from_dict(item) if isinstance(item, dict) else item
        messages.append(message)
    return messages


def dump_messages_with_checkpoints(messages: list[Message]) -> list[dict]:
    """序列化运行期消息，并把绑定的 checkpoint 段重新插回列表（简化版）。

    每条消息经 `Message.to_dict()` 转成 dict（content 为列表时逐部件
    `to_dict()`，跳过 `_no_save` 标记的部件）；若该消息绑定了
    `_checkpoint_after`，则在其后插入对应 checkpoint 消息段 dict。
    """
    dumped: list[dict] = []
    for message in messages:
        message_data = message.to_dict()
        if isinstance(message.content, list):
            message_data["content"] = [
                part.to_dict()
                for part in message.content
                if not getattr(part, "_no_save", False)
            ]
        dumped.append(message_data)
        if getattr(message, "_checkpoint_after", None) is not None:
            dumped.append(
                CheckpointMessageSegment(content=message._checkpoint_after).to_dict()
            )
    return dumped
