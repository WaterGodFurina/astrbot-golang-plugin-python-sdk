"""Agent 工具面（tool / handoff / agent / tool_executor）对齐审查。

覆盖：
- ToolExecResult 为本体同义类型别名（str | mcp CallToolResult）；
- FunctionTool.call 抛 NotImplementedError（对齐本体 tool.py:70-74）、
  泛型可下标、首参名 context；
- ToolSet 增删查/迭代/布尔语义；
- HandoffTool 构造（transfer_to_<name>、默认 schema 三键、provider_id、
  agent 属性、FunctionTool 子类）；
- Agent dataclass 字段（name/instructions/tools/run_hooks/begin_dialogs）；
- ContextWrapper 字段（context/messages/tool_call_timeout）；
- BaseFunctionToolExecutor.execute 首参约定：call 型传 run_context、
  run 型传 event（PR#2 模式在 execute 兜底路径的同语义实现）；
- FunctionToolExecutor / AstrAgentContext / AgentContextWrapper 表面。
"""
import asyncio
import os
import sys
import types
import typing
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot.core.agent.run_context import ContextWrapper  # noqa: E402
from astrbot.core.agent.tool import (  # noqa: E402
    FunctionTool,
    ToolExecResult,
    ToolSet,
)


class _CallTool(FunctionTool):
    async def call(self, context, **kwargs):
        return context


class _RunTool(FunctionTool):
    async def run(self, event, **kwargs):
        return event


class TestToolSurface(unittest.TestCase):
    def test_tool_exec_result_alias_semantics(self):
        """ToolExecResult 与本体同义：类型别名（str | mcp CallToolResult）。"""
        import typing

        import mcp.types

        args = typing.get_args(ToolExecResult)
        self.assertIn(str, args)
        self.assertIn(mcp.types.CallToolResult, args)

    def test_function_tool_call_not_implemented(self):
        """FunctionTool.call 基类抛 NotImplementedError（对齐本体语义）。"""
        tool = FunctionTool(name="t", description="d")
        with self.assertRaises(NotImplementedError):
            asyncio.run(tool.call(ContextWrapper()))

    def test_function_tool_first_param_named_context(self):
        """call 首参名为 context（对齐本体 call(context, **kwargs)）。"""
        import inspect

        params = list(inspect.signature(FunctionTool.call).parameters)
        self.assertEqual(params[1], "context")

    def test_function_tool_generic_subscription(self):
        """FunctionTool 泛型下标可用（对齐本体 Generic[TContext]）。"""
        from astrbot.core.astr_agent_context import AstrAgentContext

        self.assertIsNotNone(FunctionTool[AstrAgentContext])

    def test_toolset_basics(self):
        """ToolSet 增删查/迭代/布尔（对齐本体富 API）。"""
        ts = ToolSet()
        self.assertTrue(ts.empty())
        a = FunctionTool(name="a", description="da")
        b = FunctionTool(name="b", description="db")
        ts.add_tool(a)
        ts.add_tool(b)
        self.assertEqual(ts.names(), ["a", "b"])
        self.assertIs(ts.get_tool("a"), a)
        ts.remove_tool("a")
        self.assertIsNone(ts.get_tool("a"))
        self.assertTrue(bool(ts))
        self.assertEqual(len(ts), 1)
        self.assertEqual([t.name for t in ts], ["b"])
        self.assertEqual(len(ts.openai_schema()), 1)


class TestHandoffTool(unittest.TestCase):
    def test_construction_aligns_upstream(self):
        """构造对齐本体：transfer_to_<name> / 默认描述 / 默认参数三键。"""
        from astrbot.core.agent.agent import Agent
        from astrbot.core.agent.handoff import HandoffTool

        agent = Agent(name="writer", instructions="写作")
        tool = HandoffTool(agent=agent)
        self.assertEqual(tool.name, "transfer_to_writer")
        self.assertEqual(
            tool.description,
            "Delegate tasks to writer agent to handle the request.",
        )
        self.assertEqual(
            sorted(tool.default_parameters()["properties"]),
            ["background_task", "image_urls", "input"],
        )
        self.assertIsNone(tool.provider_id)
        self.assertIs(tool.agent, agent)
        # 对齐本体：HandoffTool 是 FunctionTool 子类
        self.assertIsInstance(tool, FunctionTool)
        # 工具描述可经 tool_description 覆盖
        tool2 = HandoffTool(agent=agent, tool_description="自定义描述")
        self.assertEqual(tool2.description, "自定义描述")


class TestAgentDataclass(unittest.TestCase):
    def test_agent_fields_align_upstream(self):
        """Agent 字段与本体一致（含 tools 注解为 list[str | FunctionTool]）。"""
        import dataclasses

        from astrbot.core.agent.agent import Agent

        names = [f.name for f in dataclasses.fields(Agent)]
        self.assertEqual(
            names,
            ["name", "instructions", "tools", "run_hooks", "begin_dialogs"],
        )
        agent = Agent(name="a")
        self.assertIsNone(agent.tools)
        self.assertIsNone(agent.run_hooks)


class TestContextWrapperFields(unittest.TestCase):
    def test_fields_align_upstream(self):
        """ContextWrapper 三字段与本体一致（context/messages/tool_call_timeout）。"""
        import dataclasses

        names = [f.name for f in dataclasses.fields(ContextWrapper)]
        self.assertEqual(names, ["context", "messages", "tool_call_timeout"])
        rc = ContextWrapper()
        self.assertEqual(rc.tool_call_timeout, 120)
        self.assertEqual(rc.messages, [])
        self.assertIsNone(rc.context)


class TestExecutorFirstArg(unittest.TestCase):
    """execute 首参约定：call 型 → run_context；run 型 → event。"""

    def _run_context(self):
        sentinel = types.SimpleNamespace(message_str="hi")
        return ContextWrapper(context=types.SimpleNamespace(event=sentinel)), sentinel

    def test_call_style_gets_run_context(self):
        """call 型工具：execute 首参为 ContextWrapper（context.context.event 可取）。"""
        from astrbot.core.agent.tool_executor import BaseFunctionToolExecutor

        rc, _sentinel = self._run_context()
        tool = _CallTool(name="call_tool", description="d")

        async def run():
            return [item async for item in BaseFunctionToolExecutor.execute(tool, rc, k=1)]

        out = asyncio.run(run())
        self.assertEqual(len(out), 1)
        self.assertIs(out[0], rc)

    def test_run_style_gets_event(self):
        """run 型工具：execute 首参为事件本身（本体 handler(event, ...) 约定）。"""
        from astrbot.core.agent.tool_executor import BaseFunctionToolExecutor

        rc, sentinel = self._run_context()
        tool = _RunTool(name="run_tool", description="d")

        async def run():
            return [item async for item in BaseFunctionToolExecutor.execute(tool, rc, k=1)]

        out = asyncio.run(run())
        self.assertEqual(len(out), 1)
        self.assertIs(out[0], sentinel)

    def test_handler_priority_and_style(self):
        """handler 优先：call 型 handler 传 run_context，run 型 handler 传 event。"""
        from astrbot.core.agent.tool_executor import BaseFunctionToolExecutor

        rc, sentinel = self._run_context()

        async def ctx_handler(context, **kw):
            return ("ctx", context)

        tool = FunctionTool(name="t1", description="d", handler=ctx_handler)

        async def run():
            return [item async for item in BaseFunctionToolExecutor.execute(tool, rc)]

        kind, got = asyncio.run(run())[0]
        self.assertEqual(kind, "ctx")
        self.assertIs(got, rc)

        async def event_handler(event, **kw):
            return ("evt", event)

        tool2 = FunctionTool(name="t2", description="d", handler=event_handler)

        async def run2():
            return [item async for item in BaseFunctionToolExecutor.execute(tool2, rc)]

        kind2, got2 = asyncio.run(run2())[0]
        self.assertEqual(kind2, "evt")
        self.assertIs(got2, sentinel)

    def test_unimplemented_tool_raises(self):
        """既无 handler 也未覆写 call()/run() → NotImplementedError。"""
        from astrbot.core.agent.tool_executor import BaseFunctionToolExecutor

        rc, _ = self._run_context()
        tool = FunctionTool(name="dead", description="d")

        async def run():
            return [item async for item in BaseFunctionToolExecutor.execute(tool, rc)]

        with self.assertRaises(NotImplementedError):
            asyncio.run(run())


class TestAgentCoreModulesSurface(unittest.TestCase):
    def test_function_tool_executor_generic(self):
        """astr_agent_tool_exec.FunctionToolExecutor 泛型参数对齐本体。"""
        from astrbot.core.agent.tool_executor import BaseFunctionToolExecutor
        from astrbot.core.astr_agent_context import AstrAgentContext
        from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor

        self.assertTrue(issubclass(FunctionToolExecutor, BaseFunctionToolExecutor))
        self.assertIs(
            FunctionToolExecutor.__orig_bases__[0].__args__[0],
            AstrAgentContext,
        )

    def test_astr_agent_context_fields(self):
        """AstrAgentContext 字段与本体一致（context/event/extra）。"""
        import dataclasses

        from astrbot.core.astr_agent_context import (
            AgentContextWrapper,
            AstrAgentContext,
        )

        self.assertEqual(
            [f.name for f in dataclasses.fields(AstrAgentContext)],
            ["context", "event", "extra"],
        )
        ctx = AstrAgentContext()
        self.assertEqual(ctx.extra, {})
        # AgentContextWrapper 是泛型别名 ContextWrapper[AstrAgentContext]
        #（对齐本体 astr_agent_context.py:21），issubclass 对 _GenericAlias
        # 会 TypeError，用 get_origin 比较来源类。
        self.assertIs(typing.get_origin(AgentContextWrapper), ContextWrapper)


if __name__ == "__main__":
    unittest.main(verbosity=2)
