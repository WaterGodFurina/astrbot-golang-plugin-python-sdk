"""Agent 运行钩子（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.agent.hooks.BaseAgentRunHooks`：插件
通常在 TYPE_CHECKING 下引用，SDK 提供可实例化的降级基类，保证
import 与构造不报错。
"""
from typing import Generic, TypeVar

_T = TypeVar("_T")


class BaseAgentRunHooks(Generic[_T]):
    """Agent 运行钩子基类（SDK 降级：所有钩子 no-op）。"""

    async def on_agent_begin(self, run_context: _T) -> None:
        """Agent 开始运行时调用（SDK 降级：no-op）。"""

    async def on_agent_message(self, run_context: _T, message) -> None:
        """Agent 产出消息时调用（SDK 降级：no-op）。"""

    async def on_agent_done(self, run_context: _T, response) -> None:
        """Agent 完成时调用（SDK 降级：no-op）。"""
