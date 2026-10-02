"""共享 Runtime 逻辑隔离测试（板块 4h/4i）。

覆盖 SessionScopedProxy + PluginSession + ContextVar 的：
- Test 1: Registry isolation（A/B 注册互不可见）
- Test 2: Handler isolation（A 清理不影响 B）
- Test 3: LLM tool isolation（A/B enumerate 各自独立）
- Test 4: Context propagation（RPC → async task → nested 仍得到 Session A）
- Test 5: Concurrent isolation（A/B 并发交错不交叉污染）
- Test 6: Legacy fallback（无 ContextVar → 模块级全局）
- Test 7: 单插件进程回归（无 session 时行为与旧路径一致）
- Test 8: 单 token 状态模型（进入 A/B → 退出 B → 仍为 A，无 plugin_id 分裂）

隔离边界：本测试只验证「逻辑级插件归属」（registry/handler/tool），不宣称
解释器级隔离（sys.modules/sys.path/C 扩展 由 python-grpc 进程级隔离承担）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot._runtime.context import (  # noqa: E402
    get_current_plugin_id,
    get_current_session,
    resolve_session_scoped,
    reset_current_session,
    set_current_session,
)
from astrbot._runtime.proxy import SessionScopedProxy  # noqa: E402
from astrbot._runtime.registry import PluginRegistry, PluginSession  # noqa: E402
from astrbot._runtime.states import PluginHealthState  # noqa: E402


def _make_session(pid: str, **kw):
    s = PluginSession(plugin_id=pid)
    # 每个 session 持有独立的 registry 实例（模拟共享 Runtime 初始化）。
    s.star_map = kw.pop("star_map", {})
    s.star_registry = kw.pop("star_registry", [])
    s.star_handlers_registry = kw.pop("star_handlers_registry", _FakeHandlerRegistry())
    s.llm_tools = kw.pop("llm_tools", _FakeToolRegistry())
    return s


class _FakeHandlerRegistry:
    """最小 handler 注册表（隔离行为对齐 StarHandlerRegistry）。"""

    def __init__(self):
        self._handlers = []

    def append(self, h):
        self._handlers.append(h)

    def remove(self, h):
        self._handlers = [x for x in self._handlers if x != h]

    def clear(self):
        self._handlers.clear()

    def all(self):
        return list(self._handlers)

    def __iter__(self):
        return iter(self._handlers)

    def __len__(self):
        return len(self._handlers)


class _FakeToolRegistry:
    """最小 LLM tool 注册表（隔离行为对齐 FunctionToolManager）。"""

    def __init__(self):
        self.tools = []

    def add_func(self, name, desc=""):
        self.tools.append((name, desc))

    def remove_func(self, name):
        self.tools = [t for t in self.tools if t[0] != name]

    def list_funcs(self):
        return list(self.tools)


class TestContextModel(unittest.TestCase):
    """Test 8：单 token 状态模型，无 plugin_id 状态分裂。"""

    def test_single_contextvar_model(self):
        a = _make_session("a")
        b = _make_session("b")
        tok_a = set_current_session(a)
        self.assertEqual(get_current_plugin_id(), "a")
        tok_b = set_current_session(b)
        self.assertEqual(get_current_plugin_id(), "b")
        # 退出 B → 恢复 A（_current_plugin_id 从唯一 session 派生，不残留）
        reset_current_session(tok_b)
        self.assertEqual(get_current_plugin_id(), "a")
        self.assertIs(get_current_session(), a)
        reset_current_session(tok_a)
        self.assertIsNone(get_current_session())
        self.assertEqual(get_current_plugin_id(), "")

    def test_set_none_resets_to_legacy(self):
        a = _make_session("a")
        tok = set_current_session(a)
        self.assertIsNotNone(get_current_session())
        reset_current_session(tok)
        self.assertIsNone(get_current_session())


class TestRegistryIsolation(unittest.TestCase):
    """Test 1/2/3：registry/handler/tool 的 A/B 隔离。"""

    def test_star_map_isolation(self):
        a = _make_session("a", star_map={})
        b = _make_session("b", star_map={})
        tok_a = set_current_session(a)
        # 有 session 时 resolve 返回 session 的独立实例
        self.assertIs(resolve_session_scoped("star_map", None), a.star_map)

        proxy_a = SessionScopedProxy(
            lambda: resolve_session_scoped("star_map", None), {}
        )
        proxy_b = SessionScopedProxy(
            lambda: resolve_session_scoped("star_map", None), {}
        )

        proxy_a["foo"] = "handler-a"
        tok_b = set_current_session(b)
        proxy_b["bar"] = "handler-b"
        # A 只能看到 foo，B 只能看到 bar
        self.assertEqual(set(a.star_map), {"foo"})
        self.assertEqual(set(b.star_map), {"bar"})
        reset_current_session(tok_b)
        self.assertEqual(proxy_a.get("foo"), "handler-a")
        self.assertNotIn("bar", a.star_map)
        reset_current_session(tok_a)

    def test_handler_cleanup_does_not_affect_other(self):
        a = _make_session("a")
        b = _make_session("b")
        ha = {"name": "handler_A"}
        hb = {"name": "handler_B"}
        tok_a = set_current_session(a)
        a.star_handlers_registry.append(ha)
        tok_b = set_current_session(b)
        b.star_handlers_registry.append(hb)
        # A 清理自己的 handler
        a.star_handlers_registry.remove(ha)
        self.assertEqual(len(a.star_handlers_registry.all()), 0)
        # B 的 handler 不受影响
        self.assertEqual(len(b.star_handlers_registry.all()), 1)
        reset_current_session(tok_b)
        reset_current_session(tok_a)

    def test_llm_tool_isolation(self):
        a = _make_session("a")
        b = _make_session("b")
        tok_a = set_current_session(a)
        a.llm_tools.add_func("tool_A")
        tok_b = set_current_session(b)
        b.llm_tools.add_func("tool_B")
        self.assertEqual([t[0] for t in a.llm_tools.list_funcs()], ["tool_A"])
        self.assertEqual([t[0] for t in b.llm_tools.list_funcs()], ["tool_B"])
        # A 删除不影响 B
        reset_current_session(tok_b)
        a.llm_tools.remove_func("tool_A")
        self.assertEqual(len(a.llm_tools.list_funcs()), 0)
        self.assertEqual(len(b.llm_tools.list_funcs()), 1)
        reset_current_session(tok_a)


class TestLegacyFallback(unittest.TestCase):
    """Test 6/7：无 session → 模块级全局（python-grpc 回归）。"""

    def test_no_session_uses_global(self):
        global fallback
        fallback_map = {"k": "v"}
        proxy = SessionScopedProxy(lambda: None, fallback_map)
        self.assertIsNone(get_current_session())
        self.assertEqual(proxy["k"], "v")
        proxy["k2"] = "v2"
        self.assertEqual(fallback_map["k2"], "v2")
        # resolve 在无 session 时返回 fallback 语义（可空则回退）
        self.assertIsNone(resolve_session_scoped("star_map", None))

    def test_session_field_unset_falls_back(self):
        # session 存在但字段未初始化 → 回退全局（与单插件一致）
        s = PluginSession(plugin_id="a")  # star_map 等字段为 None
        tok = set_current_session(s)
        self.assertIsNone(resolve_session_scoped("star_map", None))
        reset_current_session(tok)


class TestAsyncContextPropagation(unittest.TestCase):
    """Test 4：ContextVar 跨 async task / 嵌套回调传播。"""

    def test_async_task_propagates_session(self):
        import asyncio

        seen = {}

        async def nested():
            return get_current_plugin_id()

        async def worker():
            # 嵌套 task：ContextVar 在 asyncio 中自动传播
            return await asyncio.create_task(nested())

        async def run():
            return await worker()

        a = _make_session("a")
        tok = set_current_session(a)
        try:
            res = asyncio.run(run())
        finally:
            reset_current_session(tok)
        self.assertEqual(res, "a")

    def test_concurrent_isolation(self):
        """Test 5：A/B 并发交错，各自只能看到自己。"""
        import asyncio
        import threading

        a = _make_session("a")
        b = _make_session("b")
        errors = []

        async def _noop_then_plugin_id():
            await asyncio.sleep(0)
            # 经嵌套 task 再读一次：ContextVar 在 asyncio 中自动传播
            return await asyncio.create_task(_read_plugin_id())

        async def _read_plugin_id():
            return get_current_plugin_id()

        def worker(session, marker, iterations):
            try:
                for _ in range(iterations):
                    tok = set_current_session(session)
                    try:
                        # 跨新事件循环的异步边界仍应保持当前 session 传播
                        pid = asyncio.run(_noop_then_plugin_id())
                        if pid != marker:
                            errors.append(f"expected {marker}, got {pid}")
                        if get_current_plugin_id() != marker:
                            errors.append(f"ctx {get_current_plugin_id()} != {marker}")
                    finally:
                        reset_current_session(tok)
            except Exception as e:  # pragma: no cover
                errors.append(repr(e))

        threads = [
            threading.Thread(target=worker, args=(a, "a", 50)),
            threading.Thread(target=worker, args=(b, "b", 50)),
            threading.Thread(target=worker, args=(a, "a", 50)),
            threading.Thread(target=worker, args=(b, "b", 50)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertIsNone(get_current_session())


class TestProxyBasics(unittest.TestCase):
    def test_dict_ops_route(self):
        backend = {}
        proxy = SessionScopedProxy(lambda: backend, {})
        proxy["x"] = 1
        self.assertEqual(proxy["x"], 1)
        self.assertIn("x", proxy)
        self.assertEqual(len(proxy), 1)
        self.assertEqual(list(proxy), ["x"])
        del proxy["x"]
        self.assertNotIn("x", proxy)

    def test_attr_and_method_delegation(self):
        backend = _FakeToolRegistry()
        proxy = SessionScopedProxy(lambda: backend, _FakeToolRegistry())
        proxy.add_func("t", "desc")
        self.assertEqual([t[0] for t in proxy.list_funcs()], ["t"])


if __name__ == "__main__":
    unittest.main(verbosity=2)