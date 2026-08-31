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


async def run_agent(
    agent_runner: "AgentRunner",
    max_step: int = 30,
    show_tool_use: bool = True,
    show_tool_call_result: bool = False,
    stream_to_general: bool = False,
    show_reasoning: bool = False,
    buffer_intermediate_messages: bool = False,
):
    """驱动 Agent 运行器执行工具循环（对齐本体 run_agent 签名）。

    SDK 降级：宿主 Agent 编排链原生执行循环；这里迭代运行器的
    step_until_done（SDK 版为空生成器），保持可 import / 调用不抛错。
    """
    del show_tool_use, show_tool_call_result, stream_to_general, show_reasoning
    del buffer_intermediate_messages
    if agent_runner is None:
        return
        yield  # pragma: no cover - 保证是异步生成器
    async for _item in agent_runner.step_until_done(max_step):
        yield None


async def run_live_agent(
    agent_runner: "AgentRunner",
    tts_provider=None,
    max_step: int = 30,
    show_tool_use: bool = True,
    show_tool_call_result: bool = False,
    show_reasoning: bool = False,
    buffer_intermediate_messages: bool = False,
):
    """实时运行 Agent（对齐本体 run_live_agent 签名）。

    SDK 降级：与 run_agent 一致（宿主原生流式）。
    """
    del tts_provider, show_tool_use, show_tool_call_result, show_reasoning
    del buffer_intermediate_messages
    if agent_runner is None:
        return
        yield  # pragma: no cover - 保证是异步生成器
    async for _item in agent_runner.step_until_done(max_step):
        yield None


__all__ = [
    "AgentRunner",
    "run_agent",
    "run_live_agent",
    "_should_stop_agent",
    "_truncate_tool_result",
]