"""Agent 工具执行器（Go 宿主兼容运行时，对齐本体 astr_agent_tool_exec）。

`FunctionToolExecutor` 是对齐本体
`astrbot.core.astr_agent_tool_exec.FunctionToolExecutor` 的执行器子类：
SDK 在 `astrbot.api` 已提供轻量默认实现（依次尝试 handler / call() /
run()），此处保留同名子类以保证 import 路径一致。
"""
from __future__ import annotations

from astrbot.api import BaseFunctionToolExecutor


class FunctionToolExecutor(BaseFunctionToolExecutor):
    """Agent 工具执行器（SDK 默认实现，宿主工具执行在 Agent 链内）。"""


__all__ = ["FunctionToolExecutor"]