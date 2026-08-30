"""LLM 函数工具执行器（Go 宿主兼容运行时，对齐本体 agent/tool_executor）。

`BaseFunctionToolExecutor` 是工具执行器基类：Go 宿主下工具执行由宿主
HostService HandleTool RPC 原生完成；SDK 提供与本体一致的类与路径
（`astrbot.core.agent.tool_executor`），插件基于 `context.get_llm_tool_manager()`
或 `FunctionToolManager` 使用时保持一致。
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, Generic

from astrbot.core.agent.run_context import ContextWrapper, TContext
from astrbot.core.agent.tool import FunctionTool


class BaseFunctionToolExecutor(Generic[TContext]):
    """工具执行器基类（SDK 降级：宿主原生执行工具，execute 走默认回退）。"""

    @classmethod
    async def execute(
        cls,
        tool: FunctionTool,
        run_context: ContextWrapper[TContext],
        **tool_args: Any,
    ) -> AsyncGenerator[Any, None]:
        """依次尝试 handler / call() / run() 并 yield 结果（对齐 api 默认实现）。"""
        from astrbot.api import BaseFunctionToolExecutor as _Default

        async for item in _Default.execute(tool, run_context, **tool_args):
            yield item


__all__ = ["BaseFunctionToolExecutor"]