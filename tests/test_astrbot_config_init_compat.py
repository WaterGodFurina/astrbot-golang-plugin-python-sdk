"""AstrBotConfig 构造签名对齐与缺失方法补齐单测。

覆盖：
- 本体构造签名 (config_path, default_config, schema) 的按位/按名传参
  兼容（原 SDK `(*args, schema=None, **kwargs)` 下按位传 str 会
  ValueError、按名传 config_path/default_config 会 TypeError）
- Context.get_config 既有调用形态 AstrBotConfig(data, schema=schema)
  不回归（data 为 dict 位置参数）
- 本体方法面补齐：check_exist / check_config_integrity /
  _consume_reset_dashboard_password_flag
- 本体模块级常量与枚举补齐：ASTRBOT_CONFIG_PATH /
  DASHBOARD_*_ENV / RateLimitStrategy
- __delattr__ 对齐本体语义（删除后保存、缺项抛 AttributeError）
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestAstrBotConfigInitCompat(unittest.TestCase):
    def test_positional_config_path_is_str_not_dict_content(self):
        """本体形态 AstrBotConfig(path)：str 位置参 → config_path 属性，
        不再被当作 dict 序列炸 ValueError。"""
        from astrbot.core.config.astrbot_config import AstrBotConfig

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "cmd_config.json")
            cfg = AstrBotConfig(path)
            self.assertEqual(cfg.config_path, path)
            self.assertNotIn("config_path", cfg)  # 路径不得混进配置项

    def test_positional_path_and_default_config(self):
        """本体形态 AstrBotConfig(path, default_dict)：按位解析前两个参数。"""
        from astrbot.core.config.astrbot_config import AstrBotConfig

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "x.json")
            cfg = AstrBotConfig(path, {"hello": "world"})
            self.assertEqual(cfg.config_path, path)
            self.assertEqual(cfg["hello"], "world")
            self.assertEqual(cfg.default_config, {"hello": "world"})

    def test_keyword_config_path_and_default_config(self):
        """本体形态 AstrBotConfig(config_path=..., default_config=...)：
        按名传参不再 TypeError（dict() takes no keyword arguments）。"""
        from astrbot.core.config.astrbot_config import AstrBotConfig

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "k.json")
            cfg = AstrBotConfig(config_path=path, default_config={"a": 1})
            self.assertEqual(cfg.config_path, path)
            self.assertEqual(cfg["a"], 1)

    def test_dict_positional_arg_keeps_context_get_config_behavior(self):
        """Context.get_config 形态 AstrBotConfig(data, schema=schema)：
        dict 位置参仍作为初始配置全量载入。"""
        from astrbot.core.config.astrbot_config import AstrBotConfig

        data = {"provider": [], "wake_prefix": ["/"], "custom": 5}
        cfg = AstrBotConfig(data)
        self.assertEqual(cfg["custom"], 5)
        self.assertEqual(cfg["wake_prefix"], ["/"])

    def test_config_path_file_is_loaded(self):
        """显式传 config_path 且文件存在：读入文件内容（对齐本体）。"""
        from astrbot.core.config.astrbot_config import AstrBotConfig

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "plugin_conf.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"from_file": true, "n": 3}')
            cfg = AstrBotConfig(config_path=path)
            self.assertTrue(cfg["from_file"])
            self.assertEqual(cfg["n"], 3)

    def test_schema_defaults_merged_with_data(self):
        """schema 默认值入底、宿主下发的真实配置值覆盖默认值且不丢失
        （Context.get_config 场景 data 不得被 schema 丢弃）。"""
        from astrbot.core.config.astrbot_config import AstrBotConfig

        schema = {
            "name": {"type": "string", "description": "n"},
            "cnt": {"type": "int", "default": 7},
        }
        cfg = AstrBotConfig({"cnt": 9, "custom": 1}, schema=schema)
        self.assertEqual(cfg["name"], "")   # schema 默认补缺
        self.assertEqual(cfg["cnt"], 9)     # 真实值覆盖 schema 默认
        self.assertEqual(cfg["custom"], 1)  # data 键不丢
        self.assertEqual(cfg.schema, schema)

    def test_empty_construct_returns_empty_config(self):
        """无配置源构造返回空配置：宿主运行时配置由宿主下发，空构造
        不注入骨架，避免失败兜底场景 save_config 把骨架写回宿主。"""
        from astrbot.core.config.astrbot_config import AstrBotConfig

        cfg = AstrBotConfig()
        self.assertEqual(len(cfg), 0)

    def test_schema_parse_unsupported_type_raises_typeerror(self):
        """schema 含不受支持类型：抛 TypeError（对齐本体严格校验）。"""
        from astrbot.core.config.astrbot_config import AstrBotConfig

        with self.assertRaises(TypeError):
            AstrBotConfig(schema={"k": {"type": "weird_type"}})


class TestAstrBotConfigMethodSurface(unittest.TestCase):
    def test_check_exist(self):
        from astrbot.core.config.astrbot_config import AstrBotConfig

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "c.json")
            cfg = AstrBotConfig(config_path=path)
            self.assertFalse(cfg.check_exist())
            with open(path, "w", encoding="utf-8") as f:
                f.write("{}")
            self.assertTrue(cfg.check_exist())

    def test_check_exist_empty_path(self):
        from astrbot.core.config.astrbot_config import AstrBotConfig

        cfg = AstrBotConfig({"a": 1})
        object.__setattr__(cfg, "config_path", "")
        self.assertFalse(cfg.check_exist())

    def test_check_config_integrity_adds_missing_and_detects_change(self):
        from astrbot.core.config.astrbot_config import AstrBotConfig

        cfg = AstrBotConfig({"a": 1})
        refer = {"a": 1, "b": {"c": 2}}
        conf = {"a": 1}
        has_new = cfg.check_config_integrity(refer, conf)
        self.assertTrue(has_new)
        self.assertEqual(conf["b"], {"c": 2})

    def test_check_config_integrity_no_change(self):
        from astrbot.core.config.astrbot_config import AstrBotConfig

        cfg = AstrBotConfig({"a": 1})
        has_new = cfg.check_config_integrity({"a": 1}, {"a": 1})
        self.assertFalse(has_new)

    def test_check_config_integrity_none_uses_default(self):
        from astrbot.core.config.astrbot_config import AstrBotConfig

        cfg = AstrBotConfig({"a": 1})
        conf = {"a": None}
        has_new = cfg.check_config_integrity({"a": 5}, conf)
        self.assertTrue(has_new)
        self.assertEqual(conf["a"], 5)

    def test_consume_reset_dashboard_password_flag_placeholder(self):
        """占位实现可调用且消费环境变量（本体同款 env 消费行为）。"""
        from astrbot.core.config.astrbot_config import (
            DASHBOARD_RESET_PASSWORD_ENV,
            AstrBotConfig,
        )

        os.environ[DASHBOARD_RESET_PASSWORD_ENV] = "1"
        try:
            self.assertTrue(
                AstrBotConfig._consume_reset_dashboard_password_flag()
            )
            self.assertNotIn(DASHBOARD_RESET_PASSWORD_ENV, os.environ)
        finally:
            os.environ.pop(DASHBOARD_RESET_PASSWORD_ENV, None)

    def test_delattr_saves_and_missing_raises(self):
        from astrbot.core.config.astrbot_config import AstrBotConfig

        cfg = AstrBotConfig({"keep": 1, "drop": 2})
        del cfg.drop
        self.assertNotIn("drop", cfg)
        with self.assertRaises(AttributeError):
            del cfg.nonexistent_key


class TestAstrBotConfigModuleExports(unittest.TestCase):
    def test_rate_limit_strategy_enum(self):
        from astrbot.core.config.astrbot_config import RateLimitStrategy

        self.assertEqual(RateLimitStrategy.STALL.value, "stall")
        self.assertEqual(RateLimitStrategy.DISCARD.value, "discard")

    def test_rate_limit_strategy_importable_from_package(self):
        from astrbot.core.config import RateLimitStrategy  # noqa: F401

    def test_astrbot_config_path_constant(self):
        from astrbot.core.config import ASTRBOT_CONFIG_PATH  # noqa: F401
        from astrbot.core.config.astrbot_config import ASTRBOT_CONFIG_PATH

        self.assertTrue(ASTRBOT_CONFIG_PATH.endswith("cmd_config.json"))

    def test_dashboard_env_constants(self):
        from astrbot.core.config.astrbot_config import (
            DASHBOARD_INITIAL_PASSWORD_ENV,
            DASHBOARD_RESET_PASSWORD_ENV,
        )

        self.assertEqual(
            DASHBOARD_INITIAL_PASSWORD_ENV, "ASTRBOT_DASHBOARD_INITIAL_PASSWORD"
        )
        self.assertEqual(
            DASHBOARD_RESET_PASSWORD_ENV, "ASTRBOT_RESET_DASHBOARD_PASSWORD"
        )


if __name__ == "__main__":
    unittest.main()
