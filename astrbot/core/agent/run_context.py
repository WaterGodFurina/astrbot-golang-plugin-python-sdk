# Shim: 移植自原版 AstrBot astrbot/core/agent/run_context.py（MIT）
# astrbot-golang SDK 缺此模块，原版生态插件（如 livingmemory）会 import 它。
# 上游若补齐此模块，本文件可删除。
from typing import Any, Generic

from pydantic import Field
from pydantic.dataclasses import dataclass
from typing_extensions import TypeVar

from .message import Message

TContext = TypeVar("TContext", default=Any)


@dataclass
class ContextWrapper(Generic[TContext]):
    """A context for running an agent, which can be used to pass additional data or state."""

    context: TContext
    messages: list[Any] = Field(default_factory=list)  # Go 版 SDK 的 Message 非 pydantic 模型，放宽注解
    """This field stores the llm message context for the agent run, agent runners will maintain this field automatically."""
    tool_call_timeout: int = 120  # Default tool call timeout in seconds


NoContext = ContextWrapper[None]
