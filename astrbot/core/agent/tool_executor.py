"""LLM 函数工具执行器（Go 宿主兼容运行时，对齐本体 agent/tool_executor）。

`BaseFunctionToolExecutor` 是工具执行器基类：Go 宿主下工具执行由宿主
HostService HandleTool RPC 原生完成（见 internal/pipeline/stages.go 的
executePluginTool → HandleTool 链路）；本模块提供与本体一致的类与导入
路径（`astrbot.core.agent.tool_executor`）。astrbot.api 侧 re-export 本
类的同一对象（对齐本体 `from astrbot.core.agent.tool_executor import
BaseFunctionToolExecutor` 的用法），插件可继承本类并覆写 execute()
实现自己的工具调用逻辑。

首参约定对齐本体 `astr_agent_tool_exec.call_local_llm_tool`：
- call 型工具（原版 ``FunctionTool.call(context, **kwargs)``）：首参传
  ContextWrapper，插件经 ``context.context.event`` 取事件；
- run / decorator_handler 型工具：首参直接传事件。
"""
from __future__ import annotations

import inspect
from collections.abc import AsyncGenerator
from typing import Any, Generic

import mcp.types

from astrbot.core.agent.run_context import ContextWrapper, TContext
from astrbot.core.agent.tool import FunctionTool


def _tool_first_arg_is_context(handler) -> bool:
    """判断工具可调用对象首参是否为 ContextWrapper 语境（call 型）。

    判据与 `_bridge/dispatch._tool_first_arg_is_context` 保持一致
    （HandleTool RPC 路径的权威实现；此处为 execute 兜底路径的同判据副本）：

    - 首参名 context/ctx/wrapper（或注解含 ContextWrapper/AstrAgentContext）
      → call 型；
    - 首参名 event/e/msg/message（或注解含 AstrMessageEvent）→ run 型；
    - 签名不可解析时按 run 型处理（传事件，对齐旧行为）。
    """
    try:
        sig = inspect.signature(handler)
        first = next(iter(sig.parameters.values()), None)
    except (ValueError, TypeError):
        return False
    if first is None:
        return False
    name = (first.name or "").lower()
    ann = getattr(first, "annotation", None)
    ann_name = getattr(ann, "__name__", None) or str(ann or "")
    if "AstrMessageEvent" in ann_name:
        return False
    if "ContextWrapper" in ann_name or "AstrAgentContext" in ann_name:
        return True
    if name in ("event", "e", "msg", "message"):
        return False
    return name in ("context", "ctx", "wrapper")


class BaseFunctionToolExecutor(Generic[TContext]):
    """LLM 函数工具执行器基类（对齐本体签名，SDK 提供可用的兜底实现）。"""

    @classmethod
    async def execute(
        cls,
        tool: FunctionTool,
        run_context: ContextWrapper[TContext],
        **tool_args: Any,
    ) -> AsyncGenerator[Any | mcp.types.CallToolResult, None]:
        """执行一次工具调用，yield 工具结果（首参约定对齐本体）。

        依次尝试 handler / call() / run()；call 型首参传 run_context，
        run / decorator_handler 型首参传事件（``run_context.context.event``）。

        Args:
            tool: 要执行的函数工具（FunctionTool 或其子类实例）
            run_context: 运行上下文（ContextWrapper，context.event 需可用）
            tool_args: 工具调用参数

        Yields:
            工具执行结果（str / mcp CallToolResult / MessageEventResult 等）
        """
        context = getattr(run_context, "context", None)
        event = getattr(context, "event", None)

        def _first_arg(caller) -> Any:
            """按 call 型 / run 型约定选择首参。"""
            if _tool_first_arg_is_context(caller):
                return run_context
            if event is None:
                raise ValueError(
                    "Event must be provided for local function tools."
                )
            return event

        if tool.handler is not None and callable(tool.handler):
            result = tool.handler(_first_arg(tool.handler), **tool_args)
            if inspect.isawaitable(result):
                result = await result
            yield result
            return

        # 依次尝试 call() / run()：基类 call() 默认抛 NotImplementedError
        # （异步方法需 await 后才抛出），视为"未实现"并回退到下一个候选
        for caller_name in ("call", "run"):
            caller = getattr(tool, caller_name, None)
            if not callable(caller):
                continue
            try:
                result = caller(_first_arg(caller), **tool_args)
                if inspect.isawaitable(result):
                    result = await result
            except NotImplementedError:
                continue
            yield result
            return

        raise NotImplementedError(
            "工具既无 handler，也未实现 call()/run()，无法执行"
        )


__all__ = ["BaseFunctionToolExecutor"]
