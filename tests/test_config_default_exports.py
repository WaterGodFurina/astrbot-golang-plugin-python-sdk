"""astrbot.core.config 包导出面与 default.py 常量单测。

覆盖：
- 本体 config/__init__ 导出：DB_PATH / DEFAULT_CONFIG / VERSION /
  AstrBotConfig（原 SDK 缺 DEFAULT_CONFIG 导出会 ImportError）
- DEFAULT_CONFIG 顶层键集对齐本体（插件读取宿主主配置常用键不缺）
- DEFAULT_VALUE_MAP 键集对齐本体 default.py 全量类型映射
- WEBHOOK_SUPPORTED_PLATFORMS 保留
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestConfigPackageExports(unittest.TestCase):
    def test_upstream_all_exports_importable(self):
        """本体 __all__ 四项必须可从包顶层导入。"""
        from astrbot.core.config import (  # noqa: F401
            DB_PATH,
            DEFAULT_CONFIG,
            VERSION,
            AstrBotConfig,
        )

    def test_version_is_string(self):
        from astrbot.core.config import VERSION

        self.assertIsInstance(VERSION, str)
        self.assertTrue(VERSION)

    def test_db_path_layout(self):
        from astrbot.core.config import DB_PATH
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        self.assertTrue(DB_PATH.endswith("data_v4.db"))
        self.assertTrue(DB_PATH.startswith(get_astrbot_data_path()))


class TestDefaultConfigSkeleton(unittest.TestCase):
    _UPSTREAM_TOP_KEYS = {
        "config_version",
        "platform_settings",
        "provider_sources",
        "provider",
        "provider_settings",
        "subagent_orchestrator",
        "provider_stt_settings",
        "provider_tts_settings",
        "provider_ltm_settings",
        "content_safety",
        "admins_id",
        "t2i",
        "t2i_word_threshold",
        "t2i_strategy",
        "t2i_endpoint",
        "t2i_use_file_service",
        "t2i_active_template",
        "http_proxy",
        "no_proxy",
        "dashboard",
        "platform",
        "platform_specific",
        "wake_prefix",
        "log_level",
        "log_file_enable",
        "log_file_path",
        "log_file_max_mb",
        "temp_dir_max_size",
        "trace_enable",
        "trace_log_enable",
        "trace_log_path",
        "trace_log_max_mb",
        "pip_install_arg",
        "pypi_index_url",
        "persona",
        "timezone",
        "callback_api_base",
        "default_kb_collection",
        "plugin_set",
        "kb_names",
        "kb_fusion_top_k",
        "kb_final_top_k",
        "kb_agentic_mode",
        "disable_builtin_commands",
        "disable_metrics",
    }

    def test_top_keys_match_upstream(self):
        from astrbot.core.config import DEFAULT_CONFIG

        missing = self._UPSTREAM_TOP_KEYS - set(DEFAULT_CONFIG.keys())
        self.assertFalse(missing, f"DEFAULT_CONFIG 缺少本体顶层键: {missing}")

    def test_rate_limit_shape_matches_upstream(self):
        from astrbot.core.config import DEFAULT_CONFIG

        rl = DEFAULT_CONFIG["platform_settings"]["rate_limit"]
        self.assertEqual(rl["time"], 60)
        self.assertEqual(rl["count"], 30)
        self.assertEqual(rl["strategy"], "stall")

    def test_wake_prefix_is_list(self):
        """本体 wake_prefix 为列表（原 SDK 误作字符串会影响插件判断）。"""
        from astrbot.core.config import DEFAULT_CONFIG

        self.assertEqual(DEFAULT_CONFIG["wake_prefix"], ["/"])


class TestDefaultValueMap(unittest.TestCase):
    def test_value_map_matches_upstream_keys(self):
        from astrbot.core.config import DEFAULT_VALUE_MAP
        from astrbot.core.config.default import DEFAULT_VALUE_MAP as M

        self.assertIs(DEFAULT_VALUE_MAP, M)
        self.assertEqual(
            set(M.keys()),
            {"int", "float", "bool", "string", "text", "list", "file", "object",
             "template_list", "dict"},
        )
        self.assertEqual(M["int"], 0)
        self.assertEqual(M["string"], "")
        self.assertEqual(M["bool"], False)


class TestWebhookPlatformsKept(unittest.TestCase):
    def test_webhook_supported_platforms(self):
        from astrbot.core.config.default import WEBHOOK_SUPPORTED_PLATFORMS

        for p in ("qq_official_webhook", "wecom", "line"):
            self.assertIn(p, WEBHOOK_SUPPORTED_PLATFORMS)


if __name__ == "__main__":
    unittest.main()
