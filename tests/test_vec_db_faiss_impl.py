"""FaissVecDB 薄壳审查单测（不依赖 pytest：python3 tests/test_vec_db_faiss_impl.py）。

覆盖：本体 import 路径可用性（faiss_impl / faiss_impl.vec_db）、
FaissVecDB 构造签名与本体一致（k/fetch_k 参数、insert_batch 返回
list[int]、delete 返回 None、document_storage/embedding_storage 属性占位）。
"""
import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestFaissImplImport(unittest.TestCase):
    def test_import_from_faiss_impl(self):
        # 本体 faiss_impl/__init__.py 导出面：FaissVecDB
        from astrbot.core.db.vec_db.faiss_impl import FaissVecDB

        self.assertTrue(callable(FaissVecDB))

    def test_import_from_faiss_impl_vec_db(self):
        # 本体 kb_helper.py:33/185 使用的深层路径：faiss_impl.vec_db
        from astrbot.core.db.vec_db.faiss_impl.vec_db import FaissVecDB
        from astrbot.core.db.vec_db.faiss_impl import FaissVecDB as Lazy

        self.assertIs(FaissVecDB, Lazy)

    def test_base_export(self):
        from astrbot.core.db.vec_db.base import BaseVecDB, Result
        from astrbot.core.db.vec_db import BaseVecDB as PackagedBase

        self.assertIs(BaseVecDB, PackagedBase)
        r = Result(similarity=0.5, data={"id": 1})
        self.assertEqual(r.similarity, 0.5)
        self.assertEqual(r.data, {"id": 1})


class TestFaissVecDBSignature(unittest.TestCase):
    def _make(self):
        from astrbot.core.db.vec_db.faiss_impl import FaissVecDB

        return FaissVecDB("/tmp/doc.db", "/tmp/index.faiss", embedding_provider=None)

    def test_init_signature_matches_upstream(self):
        from astrbot.core.db.vec_db.faiss_impl import FaissVecDB

        params = list(inspect.signature(FaissVecDB.__init__).parameters)
        # 本体 vec_db.py:18-24：(self, doc_store_path, index_store_path,
        # embedding_provider, rerank_provider=None)
        self.assertEqual(
            params,
            ["self", "doc_store_path", "index_store_path", "embedding_provider", "rerank_provider"],
        )

    def test_retrieve_uses_k_param_like_upstream(self):
        # 本体 vec_db.py:215-222 第二参名为 k（非 base 的 top_k）
        db = self._make()
        self.assertEqual(inspect.signature(db.retrieve).parameters["k"].default, 5)
        self.assertEqual(inspect.signature(db.retrieve).parameters["fetch_k"].default, 20)
        self.assertEqual(inspect.signature(db.retrieve).parameters["rerank"].default, False)

    def test_retrieve_returns_empty_list(self):
        import asyncio

        db = self._make()
        result = asyncio.run(db.retrieve("q", k=3))
        self.assertEqual(result, [])

    def test_insert_batch_returns_list_of_int(self):
        # 本体 vec_db.py:69 返回 list[int]；降级返回 []
        import asyncio

        db = self._make()
        result = asyncio.run(db.insert_batch(["a", "b"]))
        self.assertIsInstance(result, list)
        self.assertEqual(result, [])

    def test_delete_returns_none(self):
        # 本体 vec_db.py:313-323 delete 返回 None
        import asyncio

        db = self._make()
        result = asyncio.run(db.delete("doc-1"))
        self.assertIsNone(result)

    def test_storage_attributes_placeholder(self):
        # 本体 vec_db.py:28-32 设置 document_storage/embedding_storage；
        # SDK 以 None 占位防 AttributeError
        db = self._make()
        self.assertIsNone(db.document_storage)
        self.assertIsNone(db.embedding_storage)
        self.assertEqual(db.doc_store_path, "/tmp/doc.db")
        self.assertEqual(db.index_store_path, "/tmp/index.faiss")

    def test_base_abstract_method_list(self):
        # 本体 base.py 抽象方法清单：insert/insert_batch/retrieve/delete/close
        import abc

        from astrbot.core.db.vec_db.base import BaseVecDB

        abstracts = {
            name
            for name, fn in vars(BaseVecDB).items()
            if getattr(fn, "__isabstractmethod__", False)
        }
        self.assertEqual(
            abstracts, {"insert", "insert_batch", "retrieve", "delete", "close"}
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
