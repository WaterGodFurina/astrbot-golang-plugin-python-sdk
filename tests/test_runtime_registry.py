"""共享 Runtime 骨架单元测试：状态机 / 注册表 / 会话 / Watchdog。

对应方案文档《Python 运行时多模式共存方案》第五、六、七、十节与用户修订后的
插件健康状态机。纯内部结构测试，不依赖 gRPC / 宿主。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestRuntimeStates(unittest.TestCase):
    def test_health_states_present(self):
        from astrbot._runtime.states import PluginHealthState

        self.assertEqual(PluginHealthState.NORMAL.value, "NORMAL")
        self.assertEqual(PluginHealthState.DEGRADED.value, "DEGRADED")
        self.assertEqual(PluginHealthState.UNHEALTHY.value, "UNHEALTHY")
        self.assertEqual(
            PluginHealthState.ISOLATION_PENDING.value, "ISOLATION_PENDING"
        )
        self.assertEqual(PluginHealthState.ISOLATED.value, "ISOLATED")
        self.assertEqual(
            PluginHealthState.RECOVERY_PENDING.value, "RECOVERY_PENDING"
        )
        self.assertEqual(PluginHealthState.REMOVED.value, "REMOVED")

    def test_runtime_states_present(self):
        from astrbot._runtime.states import RuntimeState

        self.assertEqual(RuntimeState.RUNNING.value, "RUNNING")
        self.assertEqual(RuntimeState.STOPPED.value, "STOPPED")
        self.assertEqual(RuntimeState.CRASHED.value, "CRASHED")

    def test_lifecycle_states_present(self):
        from astrbot._runtime.states import PluginLifecycleState

        for name in ("LOADING", "ACTIVE", "IDLE", "SLEEPING", "UNLOADED", "ERROR"):
            self.assertEqual(getattr(PluginLifecycleState, name).value, name)


class TestPluginRegistry(unittest.TestCase):
    def _session(self, pid):
        from astrbot._runtime.registry import PluginSession

        return PluginSession(plugin_id=pid, plugin_name=pid)

    def test_add_get_remove(self):
        from astrbot._runtime.registry import PluginRegistry

        reg = PluginRegistry()
        a = self._session("a")
        b = self._session("b")
        reg.add(a)
        reg.add(b)
        self.assertEqual(len(reg), 2)
        self.assertIs(reg.get("a"), a)
        self.assertIn("b", reg)
        self.assertIs(reg.remove("a"), a)
        self.assertNotIn("a", reg)
        self.assertEqual(len(reg), 1)

    def test_sessions_are_isolated(self):
        """A 的注册信息不应出现在 B（防止共用解释器后串台）。"""
        from astrbot._runtime.registry import PluginRegistry

        reg = PluginRegistry()
        a = self._session("a")
        b = self._session("b")
        a.commands["echo"] = "handler-a"
        reg.add(a)
        reg.add(b)
        self.assertEqual(reg.get("a").commands, {"echo": "handler-a"})
        self.assertEqual(reg.get("b").commands, {})

    def test_generation_and_activity(self):
        s = self._session("a")
        self.assertEqual(s.generation, 0)
        before = s.last_activity
        s.touch()
        self.assertGreaterEqual(s.last_activity, before)


class TestTaskRegistry(unittest.TestCase):
    def test_cancel_all_cancels_tasks(self):
        from astrbot._runtime.registry import TaskRegistry

        class FakeTask:
            def __init__(self):
                self.cancelled = False

            def cancel(self):
                self.cancelled = True

        tr = TaskRegistry("a")
        tasks = [FakeTask(), FakeTask()]
        for t in tasks:
            tr.register(t)
        # WeakSet：任务对象仍在作用域内 → 计数为 2
        self.assertEqual(tr.count(), 2)
        self.assertEqual(tr.cancel_all(), 2)
        self.assertTrue(all(t.cancelled for t in tasks))


class TestWatchdog(unittest.TestCase):
    def test_single_exception_does_not_isolate(self):
        from astrbot._runtime.watchdog import PluginWatchdog, CATEGORY_HANDLER
        from astrbot._runtime.states import PluginHealthState

        wd = PluginWatchdog()
        state = wd.report("a", CATEGORY_HANDLER)
        # 一次普通异常不隔离，最多 DEGRADED 以下
        self.assertIn(
            state, (PluginHealthState.NORMAL, PluginHealthState.DEGRADED)
        )

    def test_repeated_exceptions_reach_unhealthy_and_report(self):
        from astrbot._runtime.watchdog import (
            PluginWatchdog,
            WatchdogConfig,
            CATEGORY_HANDLER,
        )
        from astrbot._runtime.states import PluginHealthState

        reported = []
        wd = PluginWatchdog(
            WatchdogConfig(unhealthy_threshold=3, degraded_threshold=2),
            on_unhealthy=lambda pid, reason: reported.append((pid, reason)),
        )
        state = PluginHealthState.NORMAL
        for _ in range(3):
            state = wd.report("a", CATEGORY_HANDLER)
        self.assertEqual(state, PluginHealthState.UNHEALTHY)
        self.assertEqual(reported, [("a", "plugin_unhealthy")])
        # 重复上报不重复触发 Go 通知
        wd.report("a", CATEGORY_HANDLER)
        self.assertEqual(len(reported), 1)

    def test_sleeping_plugin_not_judged(self):
        from astrbot._runtime.watchdog import PluginWatchdog, CATEGORY_HANDLER
        from astrbot._runtime.states import (
            PluginHealthState,
            PluginLifecycleState,
        )

        wd = PluginWatchdog()
        state = wd.report(
            "a", CATEGORY_HANDLER, lifecycle=PluginLifecycleState.SLEEPING
        )
        self.assertEqual(state, PluginHealthState.NORMAL)

    def test_mark_fatal_directly_unhealthy(self):
        from astrbot._runtime.watchdog import PluginWatchdog
        from astrbot._runtime.states import PluginHealthState

        reported = []
        wd = PluginWatchdog(on_unhealthy=lambda pid, reason: reported.append(pid))
        state = wd.mark_fatal("a", "plugin_init_failed")
        self.assertEqual(state, PluginHealthState.UNHEALTHY)
        self.assertEqual(reported, ["a"])

    def test_clear_resets_window(self):
        from astrbot._runtime.watchdog import PluginWatchdog, CATEGORY_HANDLER

        wd = PluginWatchdog()
        wd.report("a", CATEGORY_HANDLER)
        wd.clear("a")
        self.assertEqual(wd.snapshot("a")["handler"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
