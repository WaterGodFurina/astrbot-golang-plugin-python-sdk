"""FunctionToolManager 权限方法 / mcp_config_path property / test_mcp_server_connection。

- _default_permission / _check_tool_permission：本体有（astrbot-py
  func_tool_manager.py:453-494），插件子类化 manager 时按本体签名覆写/调用；
- mcp_config_path：本体为 property（func_tool_manager.py:1119-1122），SDK 原为
  方法，属性访问语义已对齐；
- test_mcp_server_connection：本体 staticmethod（func_tool_manager.py:867-883）。
"""
import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot.core.provider.func_tool_manager import FunctionToolManager


class _FakeEvent:
    def __init__(self, is_admin: bool, sender_id: str = "u1"):
        self._is_admin = is_admin
        self._sender_id = sender_id

    def is_admin(self) -> bool:
        return self._is_admin

    def get_sender_id(self) -> str:
        return self._sender_id


class _FakeContext:
    def __init__(self, event):
        self.context = type("C", (), {"event": event})()


class TestDefaultPermission(unittest.TestCase):
    def test_default_is_member(self):
        """非内置工具兜底权限为 member（对齐本体 _default_permission）。"""
        self.assertEqual(FunctionToolManager()._default_permission("any_tool"), "member")


class TestCheckToolPermission(unittest.TestCase):
    def test_no_config_passes(self):
        """无 tool_permissions 配置 → 放行（返回 None）。"""
        mgr = FunctionToolManager()

        async def fake_global_get(key, default=None):
            return default

        with mock.patch(
            "astrbot.core.utils.shared_preferences.sp.global_get",
            new=fake_global_get,
        ):
            out = asyncio.run(mgr._check_tool_permission("t", _FakeContext(None)))
        self.assertIsNone(out)

    def test_admin_tool_denied_for_member(self):
        """配置 admin 且非管理员 → 返回错误串（不抛异常）。"""
        mgr = FunctionToolManager()

        async def fake_global_get(key, default=None):
            return {"_default": {"t": "admin"}}

        with mock.patch(
            "astrbot.core.utils.shared_preferences.sp.global_get",
            new=fake_global_get,
        ):
            out = asyncio.run(
                mgr._check_tool_permission("t", _FakeContext(_FakeEvent(False)))
            )
        self.assertIsInstance(out, str)
        self.assertIn("Permission denied", out)

    def test_admin_tool_allowed_for_admin(self):
        """配置 admin 且是管理员 → 放行（返回 None）。"""
        mgr = FunctionToolManager()

        async def fake_global_get(key, default=None):
            return {"_default": {"t": "admin"}}

        with mock.patch(
            "astrbot.core.utils.shared_preferences.sp.global_get",
            new=fake_global_get,
        ):
            out = asyncio.run(
                mgr._check_tool_permission("t", _FakeContext(_FakeEvent(True)))
            )
        self.assertIsNone(out)


class TestMCPConfigPath(unittest.TestCase):
    def test_is_property_not_method(self):
        """mcp_config_path 为 property：属性访问返回路径字符串（对齐本体）。"""
        mgr = FunctionToolManager()
        self.assertIsInstance(mgr.mcp_config_path, str)
        self.assertTrue(mgr.mcp_config_path.endswith("mcp_server.json"))

    def test_load_mcp_config_missing_file_returns_default(self):
        """配置文件缺失 → 返回 {"mcpServers": {}}，不写盘不抛错。"""
        import tempfile

        mgr = FunctionToolManager()
        missing = os.path.join(tempfile.gettempdir(), "no_such_mcp.json")
        with mock.patch.object(
            type(mgr),
            "mcp_config_path",
            new_callable=mock.PropertyMock,
            return_value=missing,
        ):
            self.assertEqual(mgr.load_mcp_config(), {"mcpServers": {}})


class TestSyncModelscopeStub(unittest.TestCase):
    def test_sync_stub_noop(self):
        """sync_modelscope_mcp_servers 薄壳：调用不炸。"""
        mgr = FunctionToolManager()
        asyncio.run(mgr.sync_modelscope_mcp_servers("token"))


if __name__ == "__main__":
    unittest.main()
