"""core/exceptions 异常层级补齐单测。

覆盖：
- AstrBotError 为基类；ProviderNotFoundError / EmptyModelOutputError /
  KnowledgeBaseUploadError 均为其子类（对齐本体层级）
- 插件 `except AstrBotError` 能捕获全部子类（原 SDK 缺类会 NameError）
- KnowledgeBaseUploadError 的 stage/user_message/details/__str__
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestAstrBotErrorHierarchy(unittest.TestCase):
    def test_base_class_exists(self):
        from astrbot.core.exceptions import AstrBotError

        self.assertTrue(issubclass(AstrBotError, Exception))

    def test_subclasses_inherit_astrbot_error(self):
        from astrbot.core.exceptions import (
            AstrBotError,
            EmptyModelOutputError,
            KnowledgeBaseUploadError,
            ProviderNotFoundError,
        )

        for exc_cls in (
            ProviderNotFoundError,
            EmptyModelOutputError,
            KnowledgeBaseUploadError,
        ):
            self.assertTrue(
                issubclass(exc_cls, AstrBotError),
                f"{exc_cls.__name__} 应继承 AstrBotError",
            )

    def test_except_astrbot_error_catches_all(self):
        from astrbot.core.exceptions import (
            AstrBotError,
            EmptyModelOutputError,
            KnowledgeBaseUploadError,
            ProviderNotFoundError,
        )

        for exc_cls in (ProviderNotFoundError, EmptyModelOutputError):
            with self.assertRaises(AstrBotError):
                raise exc_cls("boom")

        with self.assertRaises(AstrBotError):
            raise KnowledgeBaseUploadError(
                stage="parse", user_message="文件解析失败"
            )

    def test_knowledge_base_upload_error_fields(self):
        from astrbot.core.exceptions import KnowledgeBaseUploadError

        err = KnowledgeBaseUploadError(
            stage="embedding",
            user_message="向量化失败",
            details={"kb_id": "kb1"},
        )
        self.assertEqual(err.stage, "embedding")
        self.assertEqual(err.user_message, "向量化失败")
        self.assertEqual(err.details, {"kb_id": "kb1"})
        self.assertEqual(str(err), "向量化失败")
        # details 缺省为空 dict
        err2 = KnowledgeBaseUploadError(stage="upload", user_message="x")
        self.assertEqual(err2.details, {})

    def test_core_package_reexports(self):
        from astrbot.core import (
            AstrBotError,
            EmptyModelOutputError,
            KnowledgeBaseUploadError,
        )

        self.assertTrue(issubclass(EmptyModelOutputError, AstrBotError))
        self.assertTrue(issubclass(KnowledgeBaseUploadError, AstrBotError))


if __name__ == "__main__":
    unittest.main()
