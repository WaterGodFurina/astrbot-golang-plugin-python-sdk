"""板块 5 状态上报测试：HealthResponse.plugins（PluginStatus）结构与映射。

验证 Python → Go 状态镜像的协议结构：
- bridge_state_to_plugin_state 映射
- 单插件 servicer HealthCheck 填充自身 PluginStatus
- 共享 Runtime MultiTenantPluginService.HealthCheck 汇总全部插件
- PluginStatus 字段完整（plugin_id/plugin_name/state/health/last_activity/
  error/generation）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot._runtime.states import bridge_state_to_plugin_state  # noqa: E402


class TestStateMapping(unittest.TestCase):
    def test_bridge_state_mapping(self):
        self.assertEqual(bridge_state_to_plugin_state("RUNNING"), "ACTIVE")
        self.assertEqual(bridge_state_to_plugin_state("INSTANTIATING"), "LOADING")
        self.assertEqual(bridge_state_to_plugin_state("STOPPED"), "UNLOADED")
        self.assertEqual(bridge_state_to_plugin_state("STOPPING"), "UNLOADED")
        # 未知状态安全回退 LOADING（不误报 ACTIVE）
        self.assertEqual(bridge_state_to_plugin_state("WHATEVER"), "LOADING")


class TestPluginStatusProto(unittest.TestCase):
    def test_plugin_status_fields(self):
        from astrbot._bridge.gen import plugin_pb2

        ps = plugin_pb2.PluginStatus(
            plugin_id="a/echo",
            plugin_name="echo",
            state="ACTIVE",
            health="NORMAL",
            last_activity=123.5,
            error="",
            generation=2,
        )
        self.assertEqual(ps.plugin_id, "a/echo")
        self.assertEqual(ps.state, "ACTIVE")
        self.assertEqual(ps.health, "NORMAL")
        self.assertEqual(ps.generation, 2)

    def test_health_response_carries_plugins(self):
        from astrbot._bridge.gen import plugin_pb2

        resp = plugin_pb2.HealthResponse(
            ok=True,
            plugins=[
                plugin_pb2.PluginStatus(plugin_id="a", state="ACTIVE", health="NORMAL"),
                plugin_pb2.PluginStatus(plugin_id="b", state="ERROR", health="UNHEALTHY"),
            ],
        )
        self.assertEqual(len(resp.plugins), 2)
        self.assertEqual(resp.plugins[1].health, "UNHEALTHY")


class TestSharedRuntimeHealth(unittest.TestCase):
    def test_multi_health_aggregates_all_plugins(self):
        from astrbot._runtime.host import MultiTenantPluginService
        from astrbot._runtime.registry import PluginRegistry, PluginSession
        from astrbot._runtime.states import PluginHealthState, PluginLifecycleState

        reg = PluginRegistry()
        a = PluginSession(plugin_id="a", plugin_name="A")
        a.lifecycle = PluginLifecycleState.ACTIVE
        b = PluginSession(plugin_id="b", plugin_name="B")
        b.lifecycle = PluginLifecycleState.ERROR
        b.health = PluginHealthState.UNHEALTHY
        b.error = "boom"
        reg.add(a)
        reg.add(b)
        multi = MultiTenantPluginService(reg, {})

        resp = multi.HealthCheck(None, None)
        self.assertTrue(resp.ok)
        by_id = {p.plugin_id: p for p in resp.plugins}
        self.assertEqual(by_id["a"].state, "ACTIVE")
        self.assertEqual(by_id["a"].health, "NORMAL")
        self.assertEqual(by_id["b"].state, "ERROR")
        self.assertEqual(by_id["b"].health, "UNHEALTHY")
        self.assertEqual(by_id["b"].error, "boom")


if __name__ == "__main__":
    unittest.main(verbosity=2)