"""LLMResponse.to_openai_tool_calls(_model) 对齐本体行为（插件访问炸点修复）。"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot.core.provider.entities import LLMResponse


def _make_response(**kwargs):
    base = dict(
        role="assistant",
        tools_call_args=[{"query": "weather"}],
        tools_call_name=["get_weather"],
        tools_call_ids=["call_1"],
    )
    base.update(kwargs)
    return LLMResponse(**base)


class TestToOpenAIToolCalls(unittest.TestCase):
    def test_openai_format_payload(self):
        """dict 格式对齐本体：id/function{name,arguments}/type=function。"""
        resp = _make_response()
        out = resp.to_openai_tool_calls()
        self.assertEqual(
            out,
            [
                {
                    "id": "call_1",
                    "function": {
                        "name": "get_weather",
                        "arguments": json.dumps({"query": "weather"}),
                    },
                    "type": "function",
                }
            ],
        )

    def test_extra_content_attached(self):
        """tools_call_extra_content 按 tool_call_id 附加为 extra_content。"""
        resp = _make_response(
            tools_call_extra_content={"call_1": {"thought": "think"}}
        )
        out = resp.to_openai_tool_calls()
        self.assertEqual(out[0]["extra_content"], {"thought": "think"})

    def test_model_returns_agent_tool_call(self):
        """model 版返回 agent.message.ToolCall（id/function 结构，对齐本体）。"""
        from astrbot.core.agent.message import ToolCall

        resp = _make_response(
            tools_call_extra_content={"call_1": {"k": "v"}}
        )
        out = resp.to_openai_tool_calls_model()
        self.assertEqual(len(out), 1)
        tc = out[0]
        self.assertIsInstance(tc, ToolCall)
        self.assertEqual(tc.id, "call_1")
        self.assertEqual(tc.function["name"], "get_weather")
        self.assertEqual(
            tc.function["arguments"], json.dumps({"query": "weather"})
        )
        self.assertEqual(tc.extra_content, {"k": "v"})

    def test_legacy_misspelled_alias(self):
        """to_openai_to_calls_model 为 model 版历史拼写别名（对齐本体）。"""
        resp = _make_response()
        self.assertEqual(
            len(resp.to_openai_to_calls_model()),
            len(resp.to_openai_tool_calls_model()),
        )


if __name__ == "__main__":
    unittest.main()
