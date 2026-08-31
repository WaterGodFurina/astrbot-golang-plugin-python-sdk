"""knowledge_base_tools.py 检索降级形态对齐单测。

对齐本体 astrbot/core/tools/knowledge_base_tools.py：
- check_all_kb（本体 17-37 行）：None 计数告警、doc_count/chunk_count
  全 0 视为全空、存在非空库返回 False、空列表视为全空；
- KnowledgeBaseQueryTool.call（本体 129-142 行）：空 query 返回
  "error: Query parameter is empty."；检索无结果返回
  "No relevant knowledge found."（SDK kb_manager.retrieve 为降级恒 None，
  真实检索由宿主 Go kb_tools.go executeKBSearch 原生执行）；
- retrieve_knowledge_base：context=None 时返回 None。
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot.core.tools.knowledge_base_tools import (
    KnowledgeBaseQueryTool,
    check_all_kb,
    retrieve_knowledge_base,
)


class _KB:
    def __init__(self, doc_count=0, chunk_count=0):
        self.doc_count = doc_count
        self.chunk_count = chunk_count


class _KBHelper:
    """对齐本体 KBHelper 形态：kb 属性挂真实库对象。"""

    def __init__(self, doc_count=0, chunk_count=0):
        self.kb = _KB(doc_count, chunk_count)


class TestCheckAllKb(unittest.TestCase):
    def test_all_empty_kbs_returns_true(self):
        self.assertTrue(check_all_kb([_KBHelper(0, 0), _KBHelper(0, 0)]))

    def test_missing_kb_counts_as_empty(self):
        self.assertTrue(check_all_kb([_KBHelper(0, 0), None]))

    def test_kb_with_documents_returns_false(self):
        self.assertFalse(check_all_kb([_KBHelper(3, 0)]))
        self.assertFalse(check_all_kb([_KBHelper(0, 5)]))

    def test_none_element_does_not_crash(self):
        self.assertTrue(check_all_kb([None]))

    def test_empty_list_returns_true(self):
        self.assertTrue(check_all_kb([]))
        self.assertTrue(check_all_kb(None))

    def test_helper_without_kb_wrapper_falls_back(self):
        # 鸭子类型：无 .kb 包装的对象按自身 doc_count/chunk_count 判定
        self.assertFalse(check_all_kb([_KB(2, 0)]))


class TestKnowledgeBaseQueryToolCall(unittest.TestCase):
    def test_empty_query_error(self):
        async def run():
            return await KnowledgeBaseQueryTool().call(None)

        self.assertEqual(asyncio.run(run()), "error: Query parameter is empty.")

    def test_no_result_returns_no_knowledge_hint(self):
        async def run():
            return await KnowledgeBaseQueryTool().call(None, query="hello")

        self.assertEqual(asyncio.run(run()), "No relevant knowledge found.")

    def test_schema_surface(self):
        tool = KnowledgeBaseQueryTool()
        self.assertEqual(tool.name, "astr_kb_search")
        self.assertEqual(tool.parameters["required"], ["query"])
        self.assertIn("query", tool.parameters["properties"])


class TestRetrieveKnowledgeBase(unittest.TestCase):
    def test_none_context_returns_none(self):
        async def run():
            return await retrieve_knowledge_base(query="q", umo="umo", context=None)

        self.assertIsNone(asyncio.run(run()))

    def test_context_without_kb_manager_returns_none(self):
        async def run():
            return await retrieve_knowledge_base(query="q", umo="umo", context=object())

        self.assertIsNone(asyncio.run(run()))


if __name__ == "__main__":
    unittest.main()
