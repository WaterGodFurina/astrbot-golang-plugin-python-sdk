"""Context 管理器属性面/get_db/register_provider 对齐单测。

对齐本体 astrbot/core/star/context.py 的属性面：
- kb_manager / astrbot_config_mgr / subagent_orchestrator 属性必须存在
  （本体 context.py:163-169；SDK 修复前访问抛 AttributeError，且 SDK 自身
  knowledge_base_tools.retrieve_knowledge_base 也依赖 context.kb_manager）；
- get_db() 返回数据库占位实例（本体返回 BaseDatabase；SDK 修复前返回 None，
  插件 context.get_db().get_conversations(...) 直接 AttributeError）；
- register_provider(provider) 后 get_all_providers / get_provider_by_id
  可见（本体语义：追加进 provider 列表）；
- register_task 同时登记到本体历史命名 _register_tasks（本体 context.py:129）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestContextManagerAttrs(unittest.TestCase):
    def setUp(self):
        import astrbot.core.star.context as ctx_mod

        self.ctx_mod = ctx_mod
        self._old_bridge = ctx_mod.get_host_bridge()
        ctx_mod.set_host_bridge(None)
        self.context = ctx_mod.Context()

    def tearDown(self):
        self.ctx_mod.set_host_bridge(self._old_bridge)

    def test_kb_manager_attr_exists(self):
        """context.kb_manager 属性存在且暴露本体方法面。"""
        from astrbot.core.knowledge_base.kb_mgr import KnowledgeBaseManager

        self.assertIsInstance(self.context.kb_manager, KnowledgeBaseManager)
        for method in (
            "create_kb",
            "get_kb",
            "get_kb_by_name",
            "delete_kb",
            "list_kbs",
            "update_kb",
            "upload_from_url",
            "retrieve",
        ):
            self.assertTrue(
                callable(getattr(self.context.kb_manager, method, None)),
                f"kb_manager 缺少方法 {method}",
            )

    def test_astrbot_config_mgr_attr_exists(self):
        """context.astrbot_config_mgr 属性存在（对齐本体 context.py:163）。"""
        self.assertTrue(callable(getattr(self.context.astrbot_config_mgr, "get_conf", None)))
        self.assertTrue(callable(getattr(self.context.astrbot_config_mgr, "get_config", None)))

    def test_subagent_orchestrator_attr_exists(self):
        """context.subagent_orchestrator 属性存在（对齐本体 context.py:169）。"""
        from astrbot.core.subagent_orchestrator import SubAgentOrchestrator

        self.assertIsInstance(self.context.subagent_orchestrator, SubAgentOrchestrator)

    def test_get_db_returns_database_stub(self):
        """get_db() 返回 BaseDatabase 实例且常用方法可安全调用（不炸）。"""
        import asyncio

        from astrbot.core.db import BaseDatabase

        db = self.context.get_db()
        self.assertIsInstance(db, BaseDatabase)
        # 二次调用返回同一实例（缓存）
        self.assertIs(db, self.context.get_db())
        # 降级方法可调用且返回空结果（修复前 None.get_conversations → AttributeError）
        self.assertEqual(asyncio.run(db.get_conversations()), [])
        self.assertIsNone(asyncio.run(db.get_conversation_by_id("missing")))

    def test_register_provider_visible_in_get_all_providers(self):
        """register_provider 后 get_all_providers / get_provider_by_id 可见。"""
        from astrbot.core.provider.provider import Provider

        prov = Provider({"id": "my_custom_provider", "model": "test-model"})
        self.context.register_provider(prov)
        self.assertIn(prov, self.context.get_all_providers())
        self.assertIs(self.context.get_provider_by_id("my_custom_provider"), prov)
        # 重复注册不重复追加
        self.context.register_provider(prov)
        self.assertEqual(
            sum(1 for p in self.context.get_all_providers() if p is prov), 1
        )

    def test_register_task_appends_to_register_tasks_alias(self):
        """register_task 同时登记 _register_tasks（本体历史命名别名）。"""
        self.context.register_task(object(), "desc")
        self.assertEqual(len(self.context._register_tasks), 1)
        self.assertEqual(len(self.context._tasks), 1)
        self.assertIs(self.context._register_tasks, self.context._tasks)


if __name__ == "__main__":
    unittest.main()
