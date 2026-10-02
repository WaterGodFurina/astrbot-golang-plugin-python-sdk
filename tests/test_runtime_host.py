"""共享 Runtime Host（板块 3c）测试：按 plugin_id 多租户路由 + ContextVar 隔离。

验证：
- MultiTenantPluginService 按请求 plugin_id 路由到对应插件 servicer
- 路由前设 PluginSession ContextVar、路由后 reset（无泄漏）
- plugin_id 为空时退化为单插件进程行为（唯一 servicer）
- SharedRuntimeHost.add_plugin / remove_plugin / init_scoped_registries
- 路由过程中经 SessionScopedProxy 访问的 star_map/llm_tools/handlers 落到各自 session
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot._runtime.context import get_current_plugin_id, get_current_session  # noqa: E402
from astrbot._runtime.host import MultiTenantPluginService, SharedRuntimeHost  # noqa: E402


class _FakeRequest:
    """带 plugin_id 的伪请求（模拟各 RPC 请求消息）。"""

    def __init__(self, plugin_id: str = "", **fields):
        self.plugin_id = plugin_id
        for k, v in fields.items():
            setattr(self, k, v)


class _EchoServicer:
    """最小 servicer：记录收到的 plugin_id 与当前 ContextVar，返回回显。"""

    def __init__(self, tag: str):
        self.tag = tag
        self.calls = []

    def HandleCommand(self, request, context):
        self.calls.append(request)
        return f"{self.tag}:pid={get_current_plugin_id()}"


class TestMultiTenantRouting(unittest.TestCase):
    def _registry(self):
        from astrbot._runtime.registry import PluginRegistry

        return PluginRegistry()

    def test_routes_by_plugin_id(self):
        from astrbot._runtime.registry import PluginSession

        reg = self._registry()
        sa = PluginSession(plugin_id="a", plugin_name="A")
        sb = PluginSession(plugin_id="b", plugin_name="B")
        sa.init_scoped_registries()
        sb.init_scoped_registries()
        reg.add(sa)
        reg.add(sb)
        servicers = {"a": _EchoServicer("A"), "b": _EchoServicer("B")}
        multi = MultiTenantPluginService(reg, servicers)

        rsp_a = multi.HandleCommand(_FakeRequest(plugin_id="a"), None)
        rsp_b = multi.HandleCommand(_FakeRequest(plugin_id="b"), None)

        self.assertEqual(rsp_a, "A:pid=a")
        self.assertEqual(rsp_b, "B:pid=b")
        # 路由结束后 ContextVar 必须恢复（无泄漏）
        self.assertIsNone(get_current_session())
        self.assertEqual(get_current_plugin_id(), "")

    def test_single_tenant_fallback(self):
        """plugin_id 为空且仅一个 servicer → 退化为单插件进程行为。"""
        from astrbot._runtime.registry import PluginSession

        reg = self._registry()
        sa = PluginSession(plugin_id="a", plugin_name="A")
        reg.add(sa)
        servicers = {"a": _EchoServicer("A")}
        multi = MultiTenantPluginService(reg, servicers)

        # 单插件进程（python-grpc）宿主不带 plugin_id
        rsp = multi.HandleCommand(_FakeRequest(plugin_id=""), None)
        self.assertEqual(rsp, "A:pid=")
        self.assertIsNone(get_current_session())

    def test_unknown_plugin_id_raises(self):
        from astrbot._runtime.registry import PluginSession

        reg = self._registry()
        reg.add(PluginSession(plugin_id="a"))
        multi = MultiTenantPluginService(reg, {"a": _EchoServicer("A")})
        with self.assertRaises(KeyError):
            multi.HandleCommand(_FakeRequest(plugin_id="nope"), None)


class TestSharedRuntimeHost(unittest.TestCase):
    def test_add_remove_and_init(self):
        h = SharedRuntimeHost()
        s1 = h.add_plugin("a/echo", "echo", "/tmp/a", "1.0.0")
        s2 = h.add_plugin("b/ping", "ping", "/tmp/b", "2.0.0")
        self.assertEqual(len(h.registry), 2)
        self.assertEqual(set(h._servicers.keys()), {"a/echo", "b/ping"})
        # 未 init 前字段 None（回退全局）
        self.assertIsNone(s1.star_map)
        s1.init_scoped_registries()
        self.assertIsInstance(s1.star_map, dict)
        self.assertIsNot(s1.star_handlers_registry, s2.star_handlers_registry)
        # 独立实例（A/B 各不相同）
        h.remove_plugin("a/echo")
        self.assertEqual(len(h.registry), 1)
        self.assertIsNone(h.registry.get("a/echo"))
        self.assertIsNotNone(h.registry.get("b/ping"))

    def test_plugin_states_snapshot(self):
        h = SharedRuntimeHost()
        h.add_plugin("a/echo", "echo")
        states = h.plugin_states()
        self.assertEqual(len(states), 1)
        self.assertEqual(states[0]["plugin_id"], "a/echo")
        self.assertEqual(states[0]["state"], "LOADING")
        self.assertEqual(states[0]["health"], "NORMAL")

    def test_proxy_routes_to_session_registries(self):
        """经 SharedRuntimeHost 的 session + SessionScopedProxy：A/B 注册互不可见。"""
        from astrbot._runtime.context import set_current_session, reset_current_session

        h = SharedRuntimeHost()
        sa = h.add_plugin("a", "A")
        sb = h.add_plugin("b", "B")
        sa.init_scoped_registries()
        sb.init_scoped_registries()

        from astrbot.core.star.star import star_map
        from astrbot.core.provider.func_tool_manager import llm_tools

        tok_a = set_current_session(sa)
        star_map["a_key"] = "a_meta"
        llm_tools.add_func("tool_A", [], "desc", lambda: None)
        tok_b = set_current_session(sb)
        star_map["b_key"] = "b_meta"
        llm_tools.add_func("tool_B", [], "desc", lambda: None)
        reset_current_session(tok_b)
        reset_current_session(tok_a)

        self.assertEqual(set(sa.star_map), {"a_key"})
        self.assertEqual(set(sb.star_map), {"b_key"})
        self.assertEqual({t.name for t in sa.llm_tools.list_funcs()}, {"tool_A"})
        self.assertEqual({t.name for t in sb.llm_tools.list_funcs()}, {"tool_B"})


if __name__ == "__main__":
    unittest.main(verbosity=2)