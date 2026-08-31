"""utils.metrics Metric 公开方法面对齐本体单测。

覆盖：
- upload / flush 为可 await 的静态协程方法（原 SDK 缺 flush，插件调用
  `Metric.flush()` 会 AttributeError）
- get_installation_id 返回稳定字符串且缓存（原 SDK 缺失）
- upload(**kwargs) 任意关键字参数不炸（no-op 降级）
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot.core.utils.metrics import Metric  # noqa: E402


class TestMetricSurface(unittest.IsolatedAsyncioTestCase):
    def test_upload_is_coroutine_staticmethod(self):
        import inspect

        self.assertTrue(inspect.iscoroutinefunction(Metric.upload))

    def test_flush_is_coroutine_staticmethod(self):
        import inspect

        self.assertTrue(inspect.iscoroutinefunction(Metric.flush))

    async def test_upload_noop_accepts_any_kwargs(self):
        # 不应抛异常（本体签名 upload(**kwargs)）
        await Metric.upload(llm_tick=1, msg_event_tick=2, custom="x")

    async def test_flush_noop(self):
        await Metric.flush()

    def test_get_installation_id_stable_and_cached(self):
        # 用临时 HOME 隔离，避免污染真实 ~/.astrbot
        import tempfile

        old_home = os.environ.get("HOME")
        os.environ["HOME"] = tempfile.mkdtemp()
        try:
            Metric._iid_cache = None
            first = Metric.get_installation_id()
            second = Metric.get_installation_id()
            self.assertIsInstance(first, str)
            self.assertTrue(first)
            self.assertEqual(first, second)  # 缓存生效
            id_file = os.path.join(
                os.path.expanduser("~"), ".astrbot", ".installation_id"
            )
            self.assertTrue(os.path.exists(id_file))
        finally:
            Metric._iid_cache = None
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home


if __name__ == "__main__":
    unittest.main()
