"""Agent 运行工具函数（Go 宿主兼容运行时，对齐本体 astr_agent_run_util）。

`AgentRunner` 是主 Agent 工具循环运行器（本体为
`ToolLoopAgentRunner[AstrAgentContext]`）。宿主 Agent 编排链原生执行
完整工具循环，SDK 提供可 import 的运行器占位（agent/runners/
tool_loop_agent_runner.py 的轻量实现），插件仅在类型/标签层面引用。
"""
from __future__ import annotations

from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner

# 主 Agent 运行器别名（对齐本体 AgentRunner = ToolLoopAgentRunner[AstrAgentContext]）
AgentRunner = ToolLoopAgentRunner


def _should_stop_agent(astr_event) -> bool:
    """Agent 是否应停止（对齐本体 astr_agent_run_util._should_stop_agent）。"""
    return bool(astr_event is not None and getattr(astr_event, "is_stopped", lambda: False)())


def _truncate_tool_result(text: str, limit: int = 70) -> str:
    """工具结果截断（对齐本体同名工具，超长加省略号）。"""
    if limit <= 0 or not text:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return f"{text[: limit - 3]}..."


__all__ = [
    "AgentRunner",
    "_should_stop_agent",
    "_truncate_tool_result",
]