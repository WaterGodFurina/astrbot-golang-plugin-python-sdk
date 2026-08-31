"""db 包与 sentinels 导入路径单测（不依赖 pytest：python3 tests/test_db_sentinels.py）。

覆盖：本体 `from astrbot.core.sentinels import NOT_GIVEN` 路径可用、
db.NOT_GIVEN 与 sentinels.NOT_GIVEN 为同一对象、db 包 PO 导出面完整。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestSentinelsImport(unittest.TestCase):
    def test_sentinels_module_importable(self):
        # 本体 db/__init__.py:30 依赖 astrbot.core.sentinels.NOT_GIVEN
        from astrbot.core.sentinels import NOT_GIVEN

        self.assertIsNotNone(NOT_GIVEN)
        # 哨兵语义：对象身份唯一
        self.assertIs(NOT_GIVEN, __import__("astrbot.core.sentinels", fromlist=["x"]).NOT_GIVEN)

    def test_db_not_given_same_sentinel(self):
        # SDK db/__init__.py 的 NOT_GIVEN 与 sentinels 同一对象，
        # 插件两种 import 路径拿到的哨兵可互比（is）
        from astrbot.core.db import NOT_GIVEN as DB_NOT_GIVEN
        from astrbot.core.sentinels import NOT_GIVEN

        self.assertIs(DB_NOT_GIVEN, NOT_GIVEN)

    def test_db_export_surface(self):
        # 本体 db/__init__.py:10-29 导入的 PO 全部可经 astrbot.core.db 获取
        import astrbot.core.db as db_pkg

        for name in (
            "ApiKey",
            "Attachment",
            "BaseDatabase",
            "ChatUIProject",
            "CommandConfig",
            "CommandConflict",
            "Conversation",
            "ConversationV2",
            "CronJob",
            "NOT_GIVEN",
            "Persona",
            "PersonaFolder",
            "Personality",
            "Platform",
            "PlatformMessageHistory",
            "PlatformSession",
            "PlatformStat",
            "Preference",
            "ProviderStat",
            "SessionProjectRelation",
            "Stats",
            "TimestampMixin",
            "UmoAlias",
            "WebChatThread",
        ):
            self.assertTrue(hasattr(db_pkg, name), f"astrbot.core.db 缺 {name}")

    def test_base_database_update_persona_accepts_not_given(self):
        # 本体 db/__init__.py:454-476 update_persona 默认 NOT_GIVEN；
        # SDK 降级版传 NOT_GIVEN 不炸
        import asyncio

        from astrbot.core.db import BaseDatabase, NOT_GIVEN

        db = BaseDatabase()

        async def run():
            return await db.update_persona("p1", tools=NOT_GIVEN, skills=NOT_GIVEN)

        self.assertIsNone(asyncio.run(run()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
