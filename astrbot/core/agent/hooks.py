"""Agent 运行钩子（Go 宿主兼容运行时，对齐本体 agent/hooks）。

对齐 Python 本体 `astrbot.core.agent.hooks.BaseAgentRunHooks`：四个钩子
（on_agent_begin / on_tool_start / on_tool_end / on_agent_done）的签名
（参数名与注解）与本体一致，本体没有的 on_agent_message 此处同样不存在。
SDK 降级：所有钩子为 no-op——插件侧事件钩子经 `register_on_agent_begin`
等装饰器由宿主编排链触发，本类仅保证 import / 子类化 / 构造不报错。
"""
from __future__ import annotations

from typing import Generic

import mcp

from astrbot.core.agent.run_context import ContextWrapper, TContext
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.provider.entities import LLMResponse


class BaseAgentRunHooks(Generic[TContext]):
    """Agent 运行钩子基类（SDK 降级：所有钩子 no-op，签名对齐本体）。"""

    async def on_agent_begin(self, run_context: ContextWrapper[TContext]) -> None:
        """Agent 开始运行时调用（对齐本体 BaseAgentRunHooks.on_agent_begin，no-op）。"""

    async def on_tool_start(
        self,
        run_context: ContextWrapper[TContext],
        tool: FunctionTool,
        tool_args: dict | None,
    ) -> None:
        """调用工具前触发（对齐本体 BaseAgentRunHooks.on_tool_start，no-op）。"""

    async def on_tool_end(
        self,
        run_context: ContextWrapper[TContext],
        tool: FunctionTool,
        tool_args: dict | None,
        tool_result: mcp.types.CallToolResult | None,
    ) -> None:
        """调用工具后触发（对齐本体 BaseAgentRunHooks.on_tool_end，no-op）。"""

    async def on_agent_done(
        self,
        run_context: ContextWrapper[TContext],
        llm_response: LLMResponse,
    ) -> None:
        """Agent 完成时调用（对齐本体 BaseAgentRunHooks.on_agent_done，no-op）。"""


__all__ = ["BaseAgentRunHooks"]
