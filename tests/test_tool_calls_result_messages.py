"""ToolCallsResult.to_openai_messages_model / to_openai_messages 对齐本体。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot.core.provider.entities import ToolCallsResult


class TestToolCallsResultMessages(unittest.TestCase):
    def test_openai_messages_model_returns_segments(self):
        """model 版返回 [tool_calls_info, *tool_calls_result] 对象列表（对齐本体）。"""
        info = {"role": "assistant", "content": None, "tool_calls": []}
        r1 = {"role": "tool", "content": "res1"}
        tr = ToolCallsResult(tool_calls_info=info, tool_calls_result=[r1])
        out = tr.to_openai_messages_model()
        self.assertEqual(out, [info, r1])

    def test_openai_messages_from_flat_fields(self):
        """扁平字段（tool_call_name/args/id）→ OpenAI 消息 dict（SDK 兼容路径）。

        1 条 assistant（tool_calls 合并 name/args/id）+ 每个调用结果一条
        tool 消息（tool_calls_result 需与调用一一对应）。
        """
        tr = ToolCallsResult(
            tool_call_name=["t1", "t2"],
            tool_call_args=[{"a": 1}, {"b": 2}],
            tool_call_id=["id1", "id2"],
            tool_calls_result=[
                {"role": "tool", "content": "r1"},
                {"role": "tool", "content": "r2"},
            ],
        )
        out = tr.to_openai_messages()
        self.assertEqual(len(out), 3)
        assistant = out[0]
        self.assertEqual(assistant["role"], "assistant")
        self.assertEqual(assistant["tool_calls"][0]["id"], "id1")
        self.assertEqual(assistant["tool_calls"][1]["function"]["name"], "t2")
        self.assertEqual(out[1]["role"], "tool")
        self.assertEqual(out[2]["role"], "tool")


if __name__ == "__main__":
    unittest.main()
