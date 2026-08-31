"""computer_tools/util.py 权限与工作区路径对齐单测。

对齐本体 astrbot/core/tools/computer_tools/util.py：
- normalize_umo_for_workspace：与本体 workspace.py:22-32 同规则
  （非 [A-Za-z0-9._-] 连续折叠为 _，空 → "unknown"）；
- workspace_root：workspaces/<normalized>；
- check_admin_permission：require_admin 默认 True、role != admin 拒绝、
  require_admin=False 放行（本体语义），拒绝文案与本体一字不差；
- is_local_runtime：computer_use_runtime 默认 "local"。
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot.core.tools.computer_tools.util import (
    check_admin_permission,
    is_local_runtime,
    normalize_umo_for_workspace,
    workspace_root,
    workspace_root_for_context,
)


class _FakeEvent:
    def __init__(self, role="member", sender_id="u1"):
        self.unified_msg_origin = "aiocqhttp:GroupMessage:1001"
        self.role = role
        self._sender_id = sender_id

    def get_sender_id(self):
        return self._sender_id


class _FakeAgentContext:
    def __init__(self, role="member", settings=None, sender_id="u1"):
        self.event = _FakeEvent(role, sender_id)
        self.context = self
        self._settings = settings or {}

    def get_config(self, umo=None):
        return {"provider_settings": self._settings}


class _FakeWrapper:
    def __init__(self, role="member", settings=None):
        self.context = _FakeAgentContext(role, settings)


class TestNormalizeUmoForWorkspace(unittest.TestCase):
    def test_special_chars_folded_to_underscore(self):
        self.assertEqual(
            normalize_umo_for_workspace("aiocqhttp:GroupMessage:1001"),
            "aiocqhttp_GroupMessage_1001",
        )

    def test_exclamation_mark_folded(self):
        self.assertEqual(normalize_umo_for_workspace("a!b"), "a_b")

    def test_empty_returns_unknown(self):
        self.assertEqual(normalize_umo_for_workspace(""), "unknown")
        self.assertEqual(normalize_umo_for_workspace("   "), "unknown")

    def test_safe_chars_untouched(self):
        self.assertEqual(normalize_umo_for_workspace("abc-123._x"), "abc-123._x")


class TestWorkspaceRoot(unittest.TestCase):
    def test_root_under_workspaces_dir(self):
        root = workspace_root("aiocqhttp:GroupMessage:1")
        self.assertIn("workspaces", str(root))
        self.assertIn("aiocqhttp_GroupMessage_1", str(root))

    def test_workspace_root_for_context_uses_umo(self):
        root = asyncio.run(workspace_root_for_context(_FakeWrapper()))
        self.assertIn("aiocqhttp_GroupMessage_1001", str(root))


class TestCheckAdminPermission(unittest.TestCase):
    DENY_PREFIX = "error: Permission denied."

    def test_admin_passes(self):
        self.assertIsNone(check_admin_permission(_FakeWrapper("admin"), "Test op"))

    def test_member_denied_by_default(self):
        result = check_admin_permission(_FakeWrapper("member"), "Test op")
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith(self.DENY_PREFIX))
        self.assertIn("Test op", result)
        self.assertIn("u1", result)

    def test_member_allowed_when_require_admin_false(self):
        result = check_admin_permission(
            _FakeWrapper("member", {"computer_use_require_admin": False}), "Test op"
        )
        self.assertIsNone(result)

    def test_member_denied_when_require_admin_explicit_true(self):
        result = check_admin_permission(
            _FakeWrapper("member", {"computer_use_require_admin": True}), "Test op"
        )
        self.assertIsNotNone(result)


class TestIsLocalRuntime(unittest.TestCase):
    def test_default_is_local(self):
        self.assertTrue(is_local_runtime(_FakeWrapper()))

    def test_explicit_local(self):
        self.assertTrue(
            is_local_runtime(_FakeWrapper(settings={"computer_use_runtime": "local"}))
        )

    def test_sandbox_not_local(self):
        self.assertFalse(
            is_local_runtime(_FakeWrapper(settings={"computer_use_runtime": "sandbox"}))
        )


if __name__ == "__main__":
    unittest.main()
