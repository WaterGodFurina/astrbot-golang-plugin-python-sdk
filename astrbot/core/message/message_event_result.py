"""消息事件结果（MessageChain / MessageEventResult / CommandResult）。

对齐 Python 本体（v4.27.3）astrbot/core/message/message_event_result.py：
- MessageChain 为 dataclass，含 use_t2i_ / use_markdown_ / type 元数据字段
- ResultContentType / EventResultType 枚举对齐本体定义
- CommandResult 为 MessageEventResult 别名（无参可构造）
- get_plain_text / squash_plain / derive 语义对齐本体

同时保留 SDK 扩展：EventResultType.ASYNC_STREAM/ASYNC、
mark_llm_result / mark_model_result / set_console_log、_async_stream 旧名别名。
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from astrbot.core.message.components import (
    At,
    AtAll,
    BaseMessageComponent,
    Image,
    Json,
    Plain,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@dataclass
class MessageChain:
    """消息链：有序组件列表 + 元数据（对齐本体 dataclass 字段）。

    Attributes:
        chain: 顺序存储各个组件。
        use_t2i_: 是否使用文本转图片服务。None 为跟随用户设置。
        use_markdown_: 是否使用 Markdown 发送消息。None 跟随平台默认。
        type: 消息链承载的消息类型（可选，供平台区分业务场景）。
    """

    chain: list[BaseMessageComponent] = field(default_factory=list)
    use_t2i_: bool | None = None  # None 为跟随用户设置
    use_markdown_: bool | None = (
        None  # True 强制 Markdown，False 强制纯文本，None 跟随平台默认
    )
    type: str | None = None
    """消息链承载的消息的类型。可选，用于让消息平台区分不同业务场景的消息链。"""

    def derive(self, chain: list[BaseMessageComponent] | None = None) -> "MessageChain":
        """基于当前消息链创建新 MessageChain，继承元数据（use_t2i_ 等）。

        Args:
            chain: 新消息链的组件列表；为 None 时使用空列表（对齐本体）。
        """
        new = MessageChain(chain=chain if chain is not None else [])
        new.use_t2i_ = self.use_t2i_
        new.use_markdown_ = self.use_markdown_
        new.type = self.type
        return new

    def message(self, message: str):
        """添加一条文本消息到消息链 `chain` 中。"""
        self.chain.append(Plain(message))
        return self

    def at(self, name: str, qq: str | int):
        """添加一条 At 消息到消息链 `chain` 中。"""
        self.chain.append(At(name=name, qq=qq))
        return self

    def at_all(self):
        """添加一条 AtAll 消息到消息链 `chain` 中。"""
        self.chain.append(AtAll())
        return self

    def error(self, message: str):
        """添加一条错误消息到消息链 `chain` 中（本体已废弃，保留兼容）。"""
        self.chain.append(Plain(message))
        return self

    def url_image(self, url: str):
        """添加一条图片消息（https 链接）到消息链 `chain` 中。"""
        self.chain.append(Image.fromURL(url))
        return self

    def file_image(self, path: str):
        """添加一条图片消息（本地文件路径）到消息链 `chain` 中。"""
        self.chain.append(Image.fromFileSystem(path))
        return self

    def base64_image(self, base64_str: str):
        """添加一条图片消息（base64 编码字符串）到消息链 `chain` 中。"""
        self.chain.append(Image.fromBase64(base64_str))
        return self

    def use_t2i(self, use_t2i: bool):
        """设置是否使用文本转图片服务（写入 use_t2i_ 字段）。"""
        self.use_t2i_ = use_t2i
        return self

    def use_markdown(self, use: bool | None = True):
        """设置是否使用 Markdown 发送消息（写入 use_markdown_ 字段）。

        True 强制 Markdown，False 强制纯文本，None 跟随平台默认。
        """
        self.use_markdown_ = use
        return self

    def get_plain_text(self, with_other_comps_mark: bool = False) -> str:
        """获取纯文本消息：所有 Plain 组件文本以空格分隔拼接（对齐本体）。

        Args:
            with_other_comps_mark: 是否在纯文本中标记其他组件的位置
                （Json 输出 data 内容，其余组件输出 `[组件类名]`）。
        """
        if not with_other_comps_mark:
            return " ".join(
                [comp.text for comp in self.chain if isinstance(comp, Plain)]
            )
        texts = []
        for comp in self.chain:
            if isinstance(comp, Plain):
                texts.append(comp.text)
            elif isinstance(comp, Json):
                texts.append(f"{comp.data}")
            else:
                texts.append(f"[{comp.__class__.__name__}]")
        return " ".join(texts)

    def squash_plain(self):
        """将消息链中所有 Plain 消息段聚合到第一个 Plain 段中（对齐本体）。

        空链返回 None；否则返回 self。
        """
        if not self.chain:
            return None

        new_chain = []
        first_plain = None
        plain_texts = []

        for comp in self.chain:
            if isinstance(comp, Plain):
                if first_plain is None:
                    first_plain = comp
                    new_chain.append(comp)
                plain_texts.append(comp.text)
            else:
                new_chain.append(comp)

        if first_plain is not None:
            first_plain.text = "".join(plain_texts)

        self.chain = new_chain
        return self


class EventResultType(enum.Enum):
    """事件结果类型。

    Attributes:
        CONTINUE: 事件继续传播
        STOP: 事件停止传播
        ASYNC_STREAM / ASYNC: SDK 扩展（宿主流式/异步传输标记，本体无）
    """

    CONTINUE = enum.auto()
    """事件继续传播"""
    STOP = enum.auto()
    """事件停止传播"""
    ASYNC_STREAM = enum.auto()
    """异步流式传输（SDK 扩展）"""
    ASYNC = enum.auto()
    """异步传输（SDK 扩展）"""


class ResultContentType(enum.Enum):
    """结果的内容类型（对齐本体枚举）。

    TEXT / IMAGE / OTHER / UNKNOWN 为旧 SDK 扩展值（已废弃，保留兼容）。
    """

    LLM_RESULT = enum.auto()
    """调用 LLM 产生的结果"""
    AGENT_RUNNER_ERROR = enum.auto()
    """第三方 Agent Runner 返回的错误结果"""
    GENERAL_RESULT = enum.auto()
    """普通的消息结果"""
    STREAMING_RESULT = enum.auto()
    """调用 LLM 产生的流式结果"""
    STREAMING_FINISH = enum.auto()
    """流式输出完成"""
    # ── 旧 SDK 枚举值（已废弃，保留兼容）─────────────────────────────────
    TEXT = enum.auto()
    """[已废弃] 文本内容"""
    IMAGE = enum.auto()
    """[已废弃] 图片内容"""
    OTHER = enum.auto()
    """[已废弃] 其他内容"""
    UNKNOWN = enum.auto()
    """[已废弃] 未知内容"""


@dataclass
class MessageEventResult(MessageChain):
    """事件处理结果：组件链 + 结果类型（对齐本体 dataclass 字段）。

    Attributes:
        result_type: 事件处理的结果类型（默认 CONTINUE）。
        result_content_type: 结果的内容类型（默认 GENERAL_RESULT）。
        async_stream: 异步流（set_async_stream 写入，对齐本体公开属性）。
    """

    result_type: EventResultType | None = field(
        default_factory=lambda: EventResultType.CONTINUE,
    )
    result_content_type: ResultContentType | None = field(
        default_factory=lambda: ResultContentType.GENERAL_RESULT,
    )
    async_stream: AsyncGenerator | None = None
    """异步流"""
    # SDK 扩展私有标记（不参与 init/repr/比较，保持历史行为）
    _is_llm_result: bool = field(default=False, init=False, repr=False, compare=False)
    _is_model_result: bool = field(default=False, init=False, repr=False, compare=False)
    _console_log: str = field(default="", init=False, repr=False, compare=False)

    @property
    def _async_stream(self):
        """_async_stream 旧名别名（兼容历史代码，读写 async_stream）。"""
        return self.async_stream

    @_async_stream.setter
    def _async_stream(self, value) -> None:
        self.async_stream = value

    def set_result_type(self, result_type: EventResultType) -> "MessageEventResult":
        """设置事件结果类型（SDK 扩展）。"""
        self.result_type = result_type
        return self

    def set_console_log(self, log: str) -> "MessageEventResult":
        """设置控制台日志（SDK 扩展）。"""
        self._console_log = log
        return self

    def stop_event(self) -> "MessageEventResult":
        """终止事件传播。"""
        self.result_type = EventResultType.STOP
        return self

    def continue_event(self) -> "MessageEventResult":
        """继续事件传播。"""
        self.result_type = EventResultType.CONTINUE
        return self

    def is_stopped(self) -> bool:
        """是否终止事件传播。"""
        return self.result_type == EventResultType.STOP

    def set_async_stream(self, stream: AsyncGenerator) -> "MessageEventResult":
        """设置异步流（对齐本体：不修改 result_type）。"""
        self.async_stream = stream
        return self

    def set_result_content_type(self, typ: ResultContentType) -> "MessageEventResult":
        """设置结果的内容类型。"""
        self.result_content_type = typ
        return self

    def is_llm_result(self) -> bool:
        """是否为 LLM 结果（对齐本体：按 result_content_type 判定）。"""
        return self._is_llm_result or self.result_content_type == ResultContentType.LLM_RESULT

    def is_model_result(self) -> bool:
        """是否来自模型执行的结果（含 runner 错误，对齐本体）。"""
        return self._is_model_result or self.result_content_type in (
            ResultContentType.LLM_RESULT,
            ResultContentType.AGENT_RUNNER_ERROR,
        )

    def mark_llm_result(self) -> "MessageEventResult":
        """标记为 LLM 结果（SDK 扩展，同时写 result_content_type）。"""
        self._is_llm_result = True
        self.result_content_type = ResultContentType.LLM_RESULT
        return self

    def mark_model_result(self) -> "MessageEventResult":
        """标记为模型结果（SDK 扩展，同时写 result_content_type）。"""
        self._is_model_result = True
        self.result_content_type = ResultContentType.LLM_RESULT
        return self


# 为了兼容旧版代码，保留 CommandResult 的别名（无参可构造，对齐本体）
CommandResult = MessageEventResult
