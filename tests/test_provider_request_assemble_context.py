"""ProviderRequest.assemble_context / append_tool_calls_result 对齐本体行为。"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot.core.provider.entities import ProviderRequest, ToolCallsResult


class TestAssembleContext(unittest.TestCase):
    def test_prompt_only_degrades_to_plain_format(self):
        """仅 prompt（单文本块）→ 降级为 {"role","content"} 简单格式（对齐本体）。"""
        req = ProviderRequest(prompt="hi")
        out = asyncio.run(req.assemble_context())
        self.assertEqual(out, {"role": "user", "content": "hi"})

    def test_blank_prompt_keeps_empty_block_list(self):
        """空 prompt 无媒体 → content_blocks 为空列表，不满足单文本块降级
        条件（len==1），与本体一致返回 content=[]。"""
        req = ProviderRequest(prompt="")
        out = asyncio.run(req.assemble_context())
        self.assertEqual(out, {"role": "user", "content": []})

    def test_extra_parts_make_multimodal_format(self):
        """带 extra_user_content_parts → 返回 content 列表（多模态格式）。"""
        req = ProviderRequest(
            prompt="hi",
            extra_user_content_parts=[{"type": "text", "text": "reminder"}],
        )
        out = asyncio.run(req.assemble_context())
        self.assertEqual(out["role"], "user")
        self.assertIsInstance(out["content"], list)
        self.assertEqual(out["content"][0], {"type": "text", "text": "hi"})
        self.assertEqual(out["content"][1], {"type": "text", "text": "reminder"})

    def test_extra_part_to_dict_support(self):
        """额外内容块为带 to_dict 的对象时取其 dict 形态（对齐本体消息段兼容）。"""
        class Part:
            def to_dict(self):
                return {"type": "text", "text": "from_obj"}

        req = ProviderRequest(prompt="hi", extra_user_content_parts=[Part()])
        out = asyncio.run(req.assemble_context())
        self.assertEqual(out["content"][-1], {"type": "text", "text": "from_obj"})


class TestAppendToolCallsResult(unittest.TestCase):
    def test_append_to_empty_creates_list(self):
        """tool_calls_result 为 None 时首次 append 建列表（对齐本体语义）。"""
        req = ProviderRequest(prompt="hi")
        r1 = ToolCallsResult(tool_call_name=["t1"], tool_call_id=["id1"])
        req.append_tool_calls_result(r1)
        self.assertIsInstance(req.tool_calls_result, list)
        self.assertIs(req.tool_calls_result[0], r1)

    def test_append_promotes_single_to_list(self):
        """tool_calls_result 为单个 ToolCallsResult 时 append 升级为列表。"""
        r0 = ToolCallsResult(tool_call_name=["t0"], tool_call_id=["id0"])
        req = ProviderRequest(prompt="hi", tool_calls_result=r0)
        r1 = ToolCallsResult(tool_call_name=["t1"], tool_call_id=["id1"])
        req.append_tool_calls_result(r1)
        self.assertEqual(req.tool_calls_result, [r0, r1])

    def test_repr_and_str(self):
        """__repr__/__str__ 可用（对齐本体 ProviderRequest.__repr__ 字段面）。"""
        req = ProviderRequest(prompt="hi", session_id="s1")
        text = str(req)
        self.assertIn("ProviderRequest", text)
        self.assertIn("prompt=hi", text)


if __name__ == "__main__":
    unittest.main()
