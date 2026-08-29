"""Agent 运行上下文（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.agent.run_context` 的核心类型：
- `ContextWrapper`：运行上下文包装（泛型），字段 context / messages /
  tool_call_timeout 与本体一致；
- `TContext` / `NoContext`：泛型参数与空上下文别名。

这是 SDK 内 `ContextWrapper` 的唯一权威定义（`agent/tool.py` 的
`FunctionTool.call(context)` 签名引用此模块），避免 SDK 出现两套同名
不同义的 ContextWrapper 造成命名冲突。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from astrbot.core.agent.message import Message

# 泛型参数：标注被包装的上下文类型（如 AstrAgentContext）。
# 默认 Any，保证未标注泛型参数的上下文也能正常使用（对齐本体 default=Any）。
TContext = TypeVar("TContext", bound=Any)


@dataclass
class ContextWrapper(Generic[TContext]):
    """运行上下文包装（对齐本体 run_context.ContextWrapper）。

    插件可继承/构造此类型在工具实现中携带并访问运行期状态：
    - `context`：被包装的上下文对象（宿主注入或插件构造）；
    - `messages`：LLM 消息历史（agent runner 自动维护，通常无需插件触碰）；
    - `tool_call_timeout`：单次工具调用超时（秒，默认 120）。
    """

    context: Any = None
    """被包装的上下文对象（可空）"""
    messages: list[Message] = field(default_factory=list)
    """LLM 消息上下文（agent runner 维护，可空列表）"""
    tool_call_timeout: int = 120
    """工具调用超时（秒）"""

    def get_context(self) -> Any:
        """获取被包装的上下文对象（兼容旧简版 accessor）。"""
        return self.context

    def set_context(self, context: Any) -> None:
        """设置被包装的上下文对象（兼容旧简版 accessor）。"""
        self.context = context


# 空上下文别名（对齐本体 NoContext = ContextWrapper[None]）
NoContext = ContextWrapper


__all__ = ["ContextWrapper", "NoContext", "TContext"]