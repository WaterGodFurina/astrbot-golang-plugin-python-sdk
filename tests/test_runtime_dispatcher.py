"""共享 Runtime Dispatcher（板块 5）与 ManagePlugin 成员管理测试。

验证（方案第 5/7 节）：
- Dispatcher 经 PluginRegistry 路由，且设/复位 ContextVar（无泄漏）；
- 插件 handler 抛普通 Exception 被限制在插件边界内：记录 plugin_id、置
  session 为 ERROR、上报 Watchdog，并抛 PluginDispatchError（不杀 Runtime）；
- 其它插件不受影响（继续可路由）；
- MultiTenantPluginService.ManagePlugin 对未知 action 返回 ok=False；
  SharedRuntimeHost.manage_plugin unload 幂等、未知 action 抛错。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot._runtime.context import get_current_plugin_id, get_current_session  # noqa: E402
from astrbot._runtime.dispatcher import (  # noqa: E402
    Dispatcher,
    PluginDispatchError,
)
from astrbot._runtime.host import MultiTenantPluginService, SharedRuntimeHost  # noqa: E402
from astrbot._runtime.registry import PluginRegistry, PluginSession  # noqa: E402


class _Req:
    def __init__(self, plugin_id="", **kw):
        self.plugin_id = plugin_id
        for k, v in kw.items():
            setattr(self, k, v)


class _OkServicer:
    def __init__(self, tag):
        self.tag = tag

    def HandleCommand(self, request, context):
        return f"{self.tag}:pid={get_current_plugin_id()}"


class _BoomServicer:
    def HandleCommand(self, request, context):
        raise ValueError("boom")


class TestDispatcher(unittest.TestCase):
    def _mk(self, servicers):
        reg = PluginRegistry()
        for pid in servicers:
            s = PluginSession(plugin_id=pid, plugin_name=pid)
            s.init_scoped_registries()
            reg.add(s)
        multi = MultiTenantPluginService(reg, servicers)
        return reg, multi

    def test_routes_and_resets_contextvar(self):
        _, multi = self._mk({"a": _OkServicer("A")})
        rsp = multi.HandleCommand(_Req(plugin_id="a"), None)
        self.assertEqual(rsp, "A:pid=a")
        self.assertIsNone(get_current_session())
        self.assertEqual(get_current_plugin_id(), "")

    def test_exception_captured_per_plugin_boundary(self):
        reg, multi = self._mk({"a": _OkServicer("A"), "b": _BoomServicer()})
        # b 抛异常 → 包装为 PluginDispatchError 且记录到 session b
        with self.assertRaises(PluginDispatchError) as cm:
            multi.HandleCommand(_Req(plugin_id="b"), None)
        self.assertEqual(cm.exception.plugin_id, "b")
        sb = reg.get("b")
        self.assertEqual(sb.error, "boom")
        self.assertEqual(sb.lifecycle.value, "ERROR")
        # ContextVar 复位（异常路径也不泄漏）
        self.assertIsNone(get_current_session())
        # a 不受影响
        rsp = multi.HandleCommand(_Req(plugin_id="a"), None)
        self.assertEqual(rsp, "A:pid=a")

    def test_dispatcher_route_unknown_raises(self):
        d = Dispatcher(PluginRegistry(), {})
        with self.assertRaises(KeyError):
            d.route("nope")


class TestManagePlugin(unittest.TestCase):
    def test_manage_unknown_action_returns_error(self):
        h = SharedRuntimeHost()
        multi = h.bind_server(_FakeServer())
        resp = multi.ManagePlugin(
            _Req(action="bogus", plugin_id="a"), _NoopContext()
        )
        self.assertFalse(resp.ok)
        self.assertIn("bogus", resp.error)

    def test_manage_load_requires_dir(self):
        h = SharedRuntimeHost()
        multi = h.bind_server(_FakeServer())
        # load 一个不存在的目录 → ok=False（插件级失败不抛到 RPC 层）
        resp = multi.ManagePlugin(
            _Req(action="load", plugin_id="a", plugin_dir="/nonexistent/xyz"),
            _NoopContext(),
        )
        self.assertFalse(resp.ok)

    def test_host_manage_unload_idempotent(self):
        h = SharedRuntimeHost()
        h.add_plugin("a", "A")
        h.manage_plugin(_Req(action="unload", plugin_id="a"))
        # 再卸载一次是幂等的
        h.manage_plugin(_Req(action="unload", plugin_id="a"))
        self.assertIsNone(h.registry.get("a"))

    def test_host_manage_unknown_action_raises(self):
        h = SharedRuntimeHost()
        with self.assertRaises(ValueError):
            h.manage_plugin(_Req(action="nope", plugin_id="a"))


class _FakeServer:
    """最小 gRPC server 桩：只记录注册的 servicer。"""

    def __init__(self):
        self.servicers = []


class _NoopContext:
    def abort(self, code, msg):
        raise RuntimeError(f"abort({code}, {msg})")


# bind_server 需要 gRPC server 有 add_... 方法；这里给桩打补丁。
def _patch_fake_server():
    from astrbot._bridge.gen import plugin_pb2_grpc

    def add(servicer, server):
        server.servicers.append(servicer)

    # 仅测试用：把注册函数替换为记录版
    plugin_pb2_grpc.add_PluginServiceServicer_to_server = add


_patch_fake_server()


if __name__ == "__main__":
    unittest.main()
