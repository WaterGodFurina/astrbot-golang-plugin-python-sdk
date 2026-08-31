"""BaseAgentRunHooks 钩子签名对齐审查（对照本体 astrbot.core.agent.hooks）。

覆盖：
- 四个钩子（on_agent_begin / on_tool_start / on_tool_end / on_agent_done）
  的参数名与本体一致（run_context / tool / tool_args / tool_result /
  llm_response）；
- 钩子为 no-op 可直接 await；
- 本体没有的 on_agent_message 此处同样不存在；
- MainAgentHooks / EmptyAgentHooks / MAIN_AGENT_HOOKS 可用，泛型参数
  对齐本体 BaseAgentRunHooks[AstrAgentContext]。
"""
import asyncio
import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot.core.agent.hooks import BaseAgentRunHooks  # noqa: E402


class TestHookSignatures(unittest.TestCase):
    def test_hook_param_names_align_upstream(self):
        """四个钩子的参数名与本体逐一对齐。"""
        expected = {
            "on_agent_begin": ["run_context"],
            "on_tool_start": ["run_context", "tool", "tool_args"],
            "on_tool_end": ["run_context", "tool", "tool_args", "tool_result"],
            "on_agent_done": ["run_context", "llm_response"],
        }
        for name, params in expected.items():
            with self.subTest(hook=name):
                sig = inspect.signature(getattr(BaseAgentRunHooks, name))
                got = [
                    p.name
                    for p in sig.parameters.values()
                    if p.name != "self"
                ]
                self.assertEqual(got, params)

    def test_no_extra_on_agent_message(self):
        """本体没有 on_agent_message，SDK 也不得新增同名钩子。"""
        self.assertFalse(hasattr(BaseAgentRunHooks, "on_agent_message"))

    def test_hooks_are_noop_and_awaitable(self):
        """钩子 no-op：直接调用并 await 不抛错。"""
        from astrbot.core.agent.run_context import ContextWrapper
        from astrbot.core.agent.tool import FunctionTool

        hooks = BaseAgentRunHooks()
        rc = ContextWrapper()

        async def run():
            await hooks.on_agent_begin(rc)
            tool = FunctionTool(name="t", description="d")
            await hooks.on_tool_start(rc, tool, {"a": 1})
            await hooks.on_tool_end(rc, tool, {"a": 1}, None)
            await hooks.on_agent_done(rc, None)

        asyncio.run(run())

    def test_generic_subscription(self):
        """泛型下标 BaseAgentRunHooks[AstrAgentContext] 可用（对齐本体）。"""
        from astrbot.core.astr_agent_context import AstrAgentContext

        sub = BaseAgentRunHooks[AstrAgentContext]
        self.assertIsNotNone(sub)

    def test_main_agent_hooks_surface(self):
        """MainAgentHooks / EmptyAgentHooks / MAIN_AGENT_HOOKS 对齐本体。"""
        from astrbot.core.astr_agent_hooks import (
            MAIN_AGENT_HOOKS,
            EmptyAgentHooks,
            MainAgentHooks,
        )

        self.assertTrue(issubclass(MainAgentHooks, BaseAgentRunHooks))
        self.assertTrue(issubclass(EmptyAgentHooks, BaseAgentRunHooks))
        self.assertIsInstance(MAIN_AGENT_HOOKS, MainAgentHooks)

        async def run():
            await MAIN_AGENT_HOOKS.on_agent_begin(None)

        asyncio.run(run())  # no-op 不抛错


if __name__ == "__main__":
    unittest.main(verbosity=2)
