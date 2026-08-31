"""AstrBotConfigManager 公开方法面补齐单测。

覆盖：
- 本体符号导入：ConfInfo / DEFAULT_CONFIG_CONF_INFO（原 SDK 缺失，
  `from astrbot.core.astrbot_config_mgr import ConfInfo` 会 ImportError）
- 本体构造签名 (default_config, ucr, sp) 兼容
- default_conf 属性 / get_conf（None、str、MessageSession fallback）
- get_conf_info / get_conf_list 返回形态
- create_conf / delete_conf / update_conf_info 生命周期
  （delete/update 对 default 抛 ValueError，未知 id 返回 False）
- g() 转发语义
- initialize() 可重复调用不炸
"""
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot.core.astrbot_config_mgr import (  # noqa: E402
    DEFAULT_CONFIG_CONF_INFO,
    AstrBotConfigManager,
    ConfInfo,
)
from astrbot.core.config.astrbot_config import AstrBotConfig  # noqa: E402
from astrbot.core.platform.message_session import MessageSession  # noqa: E402


class _Run:
    """asyncio.run 的薄封装（保持 unittest 主线程简单）。"""

    @staticmethod
    def run(coro):
        return asyncio.run(coro)


class TestAstrBotConfigManagerImports(unittest.TestCase):
    def test_conf_info_importable(self):
        info = ConfInfo(id="default", name="default", path="cmd_config.json")
        self.assertEqual(info["id"], "default")

    def test_default_config_conf_info_shape(self):
        self.assertEqual(DEFAULT_CONFIG_CONF_INFO["id"], "default")
        self.assertEqual(DEFAULT_CONFIG_CONF_INFO["name"], "default")
        self.assertIn("path", DEFAULT_CONFIG_CONF_INFO)


class TestAstrBotConfigManagerCtor(unittest.TestCase):
    def test_legacy_noarg_ctor(self):
        mgr = AstrBotConfigManager()
        self.assertIsInstance(mgr.default_conf, AstrBotConfig)

    def test_upstream_ctor_signature_compat(self):
        """本体签名 AstrBotConfigManager(default_config, ucr, sp)：
        三个位置参数不再炸（原 SDK 为无参构造）。"""
        default_cfg = AstrBotConfig({"marker": "d"})
        ucr = object()
        sp = object()
        mgr = AstrBotConfigManager(default_cfg, ucr, sp)
        self.assertIs(mgr.default_conf, default_cfg)
        self.assertIs(mgr.ucr, ucr)
        self.assertIs(mgr.sp, sp)

    def test_default_config_instance_is_cached(self):
        """get_config 反复调用返回同一实例（对齐本体 confs['default']
        固定实例语义；原 SDK 每次新建实例导致插件写入静默丢失）。"""
        mgr = AstrBotConfigManager()
        self.assertIs(mgr.get_config(), mgr.get_config())
        self.assertIs(mgr.get_config(), mgr.get_conf())


class TestAstrBotConfigManagerMethods(unittest.TestCase):
    def setUp(self):
        self._old_data_path = os.environ.get("ASTRBOT_DATA_PATH")
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["ASTRBOT_DATA_PATH"] = self._tmp.name

    def tearDown(self):
        if self._old_data_path is None:
            os.environ.pop("ASTRBOT_DATA_PATH", None)
        else:
            os.environ["ASTRBOT_DATA_PATH"] = self._old_data_path
        self._tmp.cleanup()

    def test_get_conf_none_returns_default(self):
        mgr = AstrBotConfigManager()
        self.assertIs(mgr.get_conf(None), mgr.default_conf)

    def test_get_conf_unknown_umo_falls_back_to_default(self):
        """SDK 无 umo→档案绑定：未知 umo fallback 默认配置（本体兜底语义）。"""
        mgr = AstrBotConfigManager()
        self.assertIs(mgr.get_conf("aiocqhttp:GroupMessage:12345"), mgr.default_conf)

    def test_get_conf_with_message_session(self):
        mgr = AstrBotConfigManager()
        umo = MessageSession.from_str("aiocqhttp:GroupMessage:12345")
        self.assertIs(mgr.get_conf(umo), mgr.default_conf)

    def test_get_conf_info_returns_default_conf_info(self):
        mgr = AstrBotConfigManager()
        info = mgr.get_conf_info("aiocqhttp:GroupMessage:12345")
        self.assertEqual(info["id"], "default")

    def test_get_conf_list_contains_default(self):
        mgr = AstrBotConfigManager()
        lst = mgr.get_conf_list()
        self.assertTrue(any(c["id"] == "default" for c in lst))

    def test_initialize_noop_and_repeatable(self):
        mgr = AstrBotConfigManager()
        _Run.run(mgr.initialize())
        _Run.run(mgr.initialize())
        self.assertEqual(mgr.abconf_data, {})

    def test_create_conf_returns_uuid_and_persists_file(self):
        mgr = AstrBotConfigManager()
        conf_id = _Run.run(mgr.create_conf({"k": "v"}, name="myconf"))
        self.assertTrue(conf_id)
        self.assertNotEqual(conf_id, "default")
        self.assertIn(conf_id, mgr.confs)
        lst = mgr.get_conf_list()
        self.assertTrue(any(c["id"] == conf_id and c["name"] == "myconf" for c in lst))
        conf_path = os.path.join(
            os.environ["ASTRBOT_DATA_PATH"], "config", f"abconf_{conf_id}.json"
        )
        self.assertTrue(os.path.exists(conf_path))

    def test_delete_conf_default_raises(self):
        mgr = AstrBotConfigManager()
        with self.assertRaises(ValueError):
            _Run.run(mgr.delete_conf("default"))

    def test_update_conf_info_default_raises(self):
        mgr = AstrBotConfigManager()
        with self.assertRaises(ValueError):
            _Run.run(mgr.update_conf_info("default", name="x"))

    def test_delete_conf_unknown_returns_false(self):
        mgr = AstrBotConfigManager()
        self.assertFalse(_Run.run(mgr.delete_conf("nonexistent-id")))

    def test_update_conf_info_unknown_returns_false(self):
        mgr = AstrBotConfigManager()
        self.assertFalse(_Run.run(mgr.update_conf_info("nonexistent-id")))

    def test_delete_conf_lifecycle(self):
        mgr = AstrBotConfigManager()
        conf_id = _Run.run(mgr.create_conf({"k": 1}))
        self.assertTrue(_Run.run(mgr.delete_conf(conf_id)))
        self.assertNotIn(conf_id, mgr.confs)
        # 二次删除：映射已移除 → False
        self.assertFalse(_Run.run(mgr.delete_conf(conf_id)))

    def test_update_conf_info_lifecycle(self):
        mgr = AstrBotConfigManager()
        conf_id = _Run.run(mgr.create_conf({"k": 1}, name="old"))
        self.assertTrue(_Run.run(mgr.update_conf_info(conf_id, name="new")))
        names = {c["id"]: c["name"] for c in mgr.get_conf_list()}
        self.assertEqual(names[conf_id], "new")

    def test_g_reads_default_conf(self):
        mgr = AstrBotConfigManager(
            AstrBotConfig({"answer": 42})
        )
        self.assertEqual(mgr.g(key="answer"), 42)
        self.assertEqual(mgr.g(None, "answer"), 42)
        self.assertIsNone(mgr.g(None, "missing"))
        self.assertEqual(mgr.g(None, "missing", "fb"), "fb")

    def test_g_reads_umo_conf_fallback(self):
        mgr = AstrBotConfigManager(AstrBotConfig({"answer": 42}))
        self.assertEqual(mgr.g("aiocqhttp:GroupMessage:1", "answer"), 42)


if __name__ == "__main__":
    unittest.main()
