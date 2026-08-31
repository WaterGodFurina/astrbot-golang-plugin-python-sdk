"""LogManager.GetLogger 补齐单测（插件高频用法防 NameError/AttributeError）。

覆盖：
- LogManager.GetLogger 为类方法，返回标准 logging.Logger
- get_plugin_logger / get_default_logger / LogBroker 同步导出
- astrbot.core 可 `from astrbot.core import LogManager, LogBroker`
"""
import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestLogManagerGetLogger(unittest.TestCase):
    def test_get_logger_is_classmethod_returns_logger(self):
        from astrbot.core.log import LogManager

        logger = LogManager.GetLogger("unit-test.getlogger")
        self.assertIsInstance(logger, logging.Logger)
        self.assertEqual(logger.name, "unit-test.getlogger")
        # 本体签名：log_name 有缺省值
        self.assertIsInstance(LogManager.GetLogger(), logging.Logger)

    def test_plugin_logger_prefix(self):
        from astrbot.core.log import PLUGIN_LOGGER_PREFIX, LogManager

        logger = LogManager.get_plugin_logger("demo")
        self.assertEqual(logger.name, f"{PLUGIN_LOGGER_PREFIX}demo")
        self.assertEqual(PLUGIN_LOGGER_PREFIX, "astrbot.plugin.")

    def test_get_default_logger(self):
        from astrbot.core.log import LogManager

        self.assertEqual(LogManager.get_default_logger().name, "astrbot")

    def test_core_exports_log_manager_and_log_broker(self):
        from astrbot.core import LogManager as LM1, LogBroker as LB1
        from astrbot.core.log import LogManager as LM2, LogBroker as LB2

        self.assertIs(LM1, LM2)
        self.assertIs(LB1, LB2)

    def test_log_broker_stub_behaviour(self):
        from astrbot.core.log import LogBroker

        broker = LogBroker()
        q = broker.register()
        entry = {"level": "INFO", "data": "hello"}
        broker.publish(entry)
        self.assertEqual(list(broker.log_cache), [entry])
        self.assertEqual(q.qsize(), 1)
        broker.unregister(q)
        broker.publish({"level": "INFO", "data": "second"})
        self.assertEqual(len(broker.log_cache), 2)


if __name__ == "__main__":
    unittest.main()
