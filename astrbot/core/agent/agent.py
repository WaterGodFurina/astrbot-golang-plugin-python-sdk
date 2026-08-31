"""Agent 定义（Go 宿主兼容运行时，对齐本体 agent/agent）。

`Agent` 描述一个子代理：名称 / 指令 / 可用工具 / 运行钩子 / 开场对话。
Go 宿主中 agent 编排由宿主侧原生执行；SDK 在此提供与本体一致的
dataclass，供插件构造 Agent / 经 HandoffTool 注册移交工具。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic

from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.run_context import TContext
from astrbot.core.agent.tool import FunctionTool


@dataclass
class Agent(Generic[TContext]):
    """一个子代理（对齐本体 Agent dataclass）。"""

    name: str
    instructions: str | None = None
    tools: list[str | FunctionTool] | None = None
    run_hooks: BaseAgentRunHooks[TContext] | None = None
    begin_dialogs: list[Any] | None = None


__all__ = ["Agent"]