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

    async def on_agent_done(self, run_context: _T, response) -> None:
        """Agent 完成时调用（SDK 降级：no-op）。"""

    async def on_tool_start(
        self,
        run_context: _T,
        tool,
        tool_args: dict | None,
    ) -> None:
        """调用工具前触发（对齐本体 BaseAgentRunHooks.on_tool_start，no-op）。"""

    async def on_tool_end(
        self,
        run_context: _T,
        tool,
        tool_args: dict | None,
        tool_result,
    ) -> None:
        """调用工具后触发（对齐本体 BaseAgentRunHooks.on_tool_end，no-op）。"""
