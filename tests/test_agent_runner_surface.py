"""ToolLoopAgentRunner 公开方法面对齐审查（对照本体同文件）。

覆盖：
- reset 以本体式位置/关键字参数（provider, request, run_context,
  tool_executor, agent_hooks, streaming...）可调用；
- step / step_until_done 为空异步生成器（SDK 降级：不产出任何步骤），
  step_until_done 参数名为本体式 max_step；
- done / request_stop / was_aborted / get_final_llm_resp / follow_up
  签名与降级语义；
- astr_agent_run_util.run_agent / run_live_agent 可消费且不抛错。
"""
import asyncio
import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot.core.agent.runners.tool_loop_agent_runner import (  # noqa: E402
    ToolLoopAgentRunner,
)


class TestRunnerSurface(unittest.TestCase):
    def test_reset_upstream_style_call(self):
        """本体式 reset(provider, request, run_context, executor, hooks) 不抛错。"""
        from astrbot.core.agent.run_context import ContextWrapper

        runner = ToolLoopAgentRunner()
        prov, req = object(), object()
        rc = ContextWrapper()
        executor, hooks = object(), object()
        asyncio.run(
            runner.reset(
                prov,
                req,
                rc,
                executor,
                hooks,
                streaming=True,
                max_other="ignored",
            )
        )
        self.assertIs(runner.provider, prov)
        self.assertIs(runner.req, req)
        self.assertIs(runner.run_context, rc)
        self.assertIs(runner.tool_executor, executor)
        self.assertIs(runner.agent_hooks, hooks)
        self.assertTrue(runner.streaming)

    def test_step_yields_nothing(self):
        """step() 为空异步生成器（不产出任何步骤）。"""
        runner = ToolLoopAgentRunner()

        async def run():
            return [item async for item in runner.step()]

        self.assertEqual(asyncio.run(run()), [])

    def test_step_until_done_max_step_kwarg(self):
        """step_until_done 参数名为本体式 max_step，空产出后 done() 为 True。"""
        runner = ToolLoopAgentRunner()
        self.assertFalse(runner.done())

        async def run():
            return [item async for item in runner.step_until_done(max_step=3)]

        self.assertEqual(asyncio.run(run()), [])
        self.assertTrue(runner.done())
        # 位置参数同样可用（本体 step_until_done(30) 风格）
        runner2 = ToolLoopAgentRunner()

        async def run2():
            return [item async for item in runner2.step_until_done(30)]

        self.assertEqual(asyncio.run(run2()), [])

    def test_stop_and_abort_surface(self):
        """request_stop / was_aborted / get_final_llm_resp 降级语义。"""
        runner = ToolLoopAgentRunner()
        self.assertIsNone(runner.get_final_llm_resp())
        self.assertFalse(runner.was_aborted())
        runner.request_stop()  # 不抛错即可

    def test_follow_up_keyword_only(self):
        """follow_up(message_text=...) 关键字限定，SDK 降级恒返回 None。"""
        runner = ToolLoopAgentRunner()
        self.assertIsNone(runner.follow_up(message_text="补充消息"))

    def test_run_agent_and_live_agent_consume_cleanly(self):
        """run_agent / run_live_agent 空转可消费、不抛错。"""
        from astrbot.core.astr_agent_run_util import run_agent, run_live_agent

        async def consume(gen):
            return [item async for item in gen]

        runner = ToolLoopAgentRunner()
        self.assertEqual(
            asyncio.run(
                consume(run_agent(runner, 30, True, False, False, False, False))
            ),
            [],
        )
        self.assertEqual(
            asyncio.run(consume(run_agent(None))),
            [],
        )
        self.assertEqual(
            asyncio.run(consume(run_live_agent(runner, None, 30))),
            [],
        )


class TestRunUtilSignature(unittest.TestCase):
    def test_run_agent_params_align_upstream(self):
        """run_agent / run_live_agent 参数名与顺序对齐本体。"""
        from astrbot.core import astr_agent_run_util as util

        self.assertEqual(
            list(inspect.signature(util.run_agent).parameters),
            [
                "agent_runner",
                "max_step",
                "show_tool_use",
                "show_tool_call_result",
                "stream_to_general",
                "show_reasoning",
                "buffer_intermediate_messages",
            ],
        )
        self.assertEqual(
            list(inspect.signature(util.run_live_agent).parameters),
            [
                "agent_runner",
                "tts_provider",
                "max_step",
                "show_tool_use",
                "show_tool_call_result",
                "show_reasoning",
                "buffer_intermediate_messages",
            ],
        )
        # AgentRunner 别名可用（对齐本体 AgentRunner = ToolLoopAgentRunner[...]）
        self.assertIs(util.AgentRunner, ToolLoopAgentRunner)


if __name__ == "__main__":
    unittest.main(verbosity=2)
