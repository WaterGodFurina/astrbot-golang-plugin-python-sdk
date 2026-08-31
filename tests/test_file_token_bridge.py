"""file_token_service.py 宿主桥真实现（RegisterFileToken）的单测。

覆盖点：
- register_file：转发宿主 register_file_token（path/timeout_sec 透传、
  timeout→timeout_sec 映射）、token 为空串返回 None、宿主不可用 → None、
  旧宿主桥（无该方法）→ None；
- get_url_from_file_path：先登记拿 token → 拼 callback_api_base +
  /api/file/{token}；callback_api_base 为空 → None；token 登记失败 → None。

运行：python3 tests/test_file_token_bridge.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _BridgeTestCase(unittest.IsolatedAsyncioTestCase):
    """提供 patch astrbot.core.star.context.get_host_bridge 的基类
    （FakeBridge 注入模式对齐 tests/test_skill_manager_alignment.py）。"""

    def _fake_bridge(self, **overrides):
        methods = {
            "ensure_connected": lambda self: True,
            "get_config": lambda self, plugin_name="": {},
        }
        methods.update(overrides)
        return type("FakeBridge", (), methods)()

    def _patch_bridge(self, fake):
        import astrbot.core.star.context as ctx_mod

        old = ctx_mod.get_host_bridge
        ctx_mod.get_host_bridge = lambda: fake
        self.addCleanup(lambda: setattr(ctx_mod, "get_host_bridge", old))


def _svc():
    from astrbot.core.file_token_service import FileTokenService

    return FileTokenService()


class TestRegisterFileBridge(_BridgeTestCase):
    """register_file 真实现。"""

    async def test_register_file_forwards_host_token(self):
        calls = {}

        def register_file_token(self, path="", timeout_sec=0):
            calls["args"] = (path, timeout_sec)
            return "tok123"

        self._patch_bridge(
            self._fake_bridge(register_file_token=register_file_token)
        )
        token = await _svc().register_file("/tmp/a.txt")
        self.assertEqual(token, "tok123")
        self.assertEqual(calls["args"], ("/tmp/a.txt", 0))

    async def test_register_file_maps_timeout_to_timeout_sec(self):
        calls = {}

        def register_file_token(self, path="", timeout_sec=0):
            calls["timeout_sec"] = timeout_sec
            return "tok"

        self._patch_bridge(
            self._fake_bridge(register_file_token=register_file_token)
        )
        await _svc().register_file("/tmp/a.txt", timeout=60)
        self.assertEqual(calls["timeout_sec"], 60)

    async def test_register_file_empty_token_returns_none(self):
        self._patch_bridge(
            self._fake_bridge(register_file_token=lambda self, path="", timeout_sec=0: "")
        )
        self.assertIsNone(await _svc().register_file("/tmp/a.txt"))

    async def test_register_file_host_unavailable_returns_none(self):
        self._patch_bridge(None)
        self.assertIsNone(await _svc().register_file("/tmp/a.txt"))

    async def test_register_file_old_bridge_without_method_degrades(self):
        """旧宿主桥（无 register_file_token）→ None，不抛 AttributeError。"""
        self._patch_bridge(self._fake_bridge())
        self.assertIsNone(await _svc().register_file("/tmp/a.txt"))


class TestGetUrlFromFilePath(_BridgeTestCase):
    """get_url_from_file_path：token → callback_api_base 拼接。"""

    async def test_url_is_callback_base_plus_api_file_token(self):
        self._patch_bridge(
            self._fake_bridge(
                register_file_token=lambda self, path="", timeout_sec=0: "tok123",
                get_config=lambda self, plugin_name="": {
                    "callback_api_base": "http://cb.example"
                },
            )
        )
        url = await _svc().get_url_from_file_path("/tmp/a.txt")
        self.assertEqual(url, "http://cb.example/api/file/tok123")

    async def test_url_strips_trailing_slash_of_callback_base(self):
        self._patch_bridge(
            self._fake_bridge(
                register_file_token=lambda self, path="", timeout_sec=0: "tok",
                get_config=lambda self, plugin_name="": {
                    "callback_api_base": "http://cb.example/"
                },
            )
        )
        url = await _svc().get_url_from_file_path("/tmp/a.txt")
        self.assertEqual(url, "http://cb.example/api/file/tok")

    async def test_empty_callback_api_base_returns_none(self):
        """callback_api_base 未配置（宿主 config 为空）→ None。"""
        self._patch_bridge(
            self._fake_bridge(
                register_file_token=lambda self, path="", timeout_sec=0: "tok123",
                get_config=lambda self, plugin_name="": {},
            )
        )
        self.assertIsNone(await _svc().get_url_from_file_path("/tmp/a.txt"))

    async def test_register_failure_returns_none(self):
        """token 登记失败（宿主不可用）→ None，不抛。"""
        self._patch_bridge(None)
        self.assertIsNone(await _svc().get_url_from_file_path("/tmp/a.txt"))


class TestDegradedMethods(_BridgeTestCase):
    """check_token_expired / handle_file 保持降级（token 表在宿主侧）。"""

    async def test_check_token_expired_still_true(self):
        self._patch_bridge(self._fake_bridge())
        self.assertTrue(await _svc().check_token_expired("tok"))

    async def test_handle_file_still_none(self):
        self._patch_bridge(self._fake_bridge())
        self.assertIsNone(await _svc().handle_file("tok"))


if __name__ == "__main__":
    unittest.main()
