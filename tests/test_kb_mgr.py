"""KnowledgeBaseManager 签名对齐单测（不依赖 pytest：python3 tests/test_kb_mgr.py）。

覆盖：create_kb/update_kb 检索参数（top_k_dense/top_k_sparse/top_m_final）、
upload_from_url progress_callback 参数、__init__ provider_manager 兼容、
_format_context 纯本地格式化实现（对齐本体 kb_mgr.py:342-361）。
"""
import inspect
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _params_of(func):
    return list(inspect.signature(func).parameters)


class TestKBMgrSignature(unittest.TestCase):
    def test_init_accepts_provider_manager(self):
        # 本体 kb_mgr.py:27-36 __init__(provider_manager) 必传
        from astrbot.core.knowledge_base.kb_mgr import KnowledgeBaseManager

        self.assertIn("provider_manager", _params_of(KnowledgeBaseManager.__init__))
        mgr = KnowledgeBaseManager(provider_manager=None)
        self.assertIsNone(mgr.provider_manager)
        mgr2 = KnowledgeBaseManager()
        self.assertIsNone(mgr2.provider_manager)

    def test_create_kb_has_retrieval_params(self):
        # 本体 kb_mgr.py:87-99
        from astrbot.core.knowledge_base.kb_mgr import KnowledgeBaseManager

        params = _params_of(KnowledgeBaseManager.create_kb)
        for name in (
            "kb_name",
            "description",
            "emoji",
            "embedding_provider_id",
            "rerank_provider_id",
            "chunk_size",
            "chunk_overlap",
            "top_k_dense",
            "top_k_sparse",
            "top_m_final",
        ):
            self.assertIn(name, params, f"create_kb 缺参数 {name}")

    def test_update_kb_has_retrieval_params(self):
        # 本体 kb_mgr.py:187-200
        from astrbot.core.knowledge_base.kb_mgr import KnowledgeBaseManager

        params = _params_of(KnowledgeBaseManager.update_kb)
        for name in ("top_k_dense", "top_k_sparse", "top_m_final"):
            self.assertIn(name, params, f"update_kb 缺参数 {name}")

    def test_upload_from_url_accepts_progress_callback(self):
        # 本体 kb_mgr.py:380-390 末参 progress_callback
        from astrbot.core.knowledge_base.kb_mgr import KnowledgeBaseManager

        params = _params_of(KnowledgeBaseManager.upload_from_url)
        self.assertIn("progress_callback", params)
        sig = inspect.signature(KnowledgeBaseManager.upload_from_url)
        self.assertEqual(sig.parameters["chunk_size"].default, 512)
        self.assertEqual(sig.parameters["chunk_overlap"].default, 50)

    def test_retrieve_signature_matches_upstream(self):
        # 本体 kb_mgr.py:282-288
        from astrbot.core.knowledge_base.kb_mgr import KnowledgeBaseManager

        sig = inspect.signature(KnowledgeBaseManager.retrieve)
        self.assertEqual(sig.parameters["top_k_fusion"].default, 20)
        self.assertEqual(sig.parameters["top_m_final"].default, 5)

    def test_degraded_calls_do_not_raise(self):
        # 薄壳调用不炸：create_kb/update_kb/upload_from_url/retrieve
        import asyncio

        from astrbot.core.knowledge_base.kb_mgr import KnowledgeBaseManager

        mgr = KnowledgeBaseManager()

        async def run():
            await mgr.create_kb(
                "kb1",
                top_k_dense=10,
                top_k_sparse=10,
                top_m_final=3,
            )
            await mgr.update_kb("id", "kb1", top_m_final=3)
            await mgr.upload_from_url("id", "https://x", progress_callback=lambda c, t: None)
            return await mgr.retrieve("q", ["kb1"])

        self.assertIsNone(asyncio.run(run()))

    def test_format_context(self):
        # 本体 kb_mgr.py:342-361 纯本地格式化
        from astrbot.core.knowledge_base.kb_mgr import KnowledgeBaseManager

        mgr = KnowledgeBaseManager()
        results = [
            SimpleNamespace(
                kb_name="手册", doc_name="faq.md", content="如何重启", score=0.9166666
            )
        ]
        text = mgr._format_context(results)
        self.assertIn("【知识 1】", text)
        self.assertIn("来源: 手册 / faq.md", text)
        self.assertIn("内容: 如何重启", text)
        self.assertIn("相关度: 0.92", text)
        empty = mgr._format_context([])
        # 本体：空结果仍返回引导行
        self.assertTrue(empty.startswith("以下是相关的知识库内容"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
