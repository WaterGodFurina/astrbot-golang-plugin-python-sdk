"""Context.get_platform / get_platform_inst 平台查找对齐单测。

对齐本体 astrbot/core/star/context.py 的 get_platform(platform_type)
（本体按 meta().name 匹配字符串或 PlatformAdapterType 枚举位）与
get_platform_inst(platform_id)（本体按 meta().id 匹配）。SDK 修复前
两方法恒返回 None（插件 context.get_platform("aiocqhttp").bot 等用法
静默失败）；修复后经宿主 ListPlatforms 构造的平台占位实例查找。

同时覆盖：context.platform_manager 应为 manager.PlatformManager 实例
（对齐本体类型关系，isinstance 可命中）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _FakeBridge:
    """宿主桥桩：ensure_connected / list_platforms 返回固定平台清单。"""

    def ensure_connected(self):
        return True

    def list_platforms(self):
        return [
            {"id": "aiocqhttp_main", "type": "aiocqhttp", "name": "aiocqhttp"},
            {"id": "webchat_inst", "type": "webchat", "name": "webchat"},
        ]


class TestContextPlatformLookup(unittest.TestCase):
    def setUp(self):
        import astrbot.core.star.context as ctx_mod

        self.ctx_mod = ctx_mod
        self._old_bridge = ctx_mod.get_host_bridge()
        ctx_mod.set_host_bridge(_FakeBridge())
        self.context = ctx_mod.Context()

    def tearDown(self):
        self.ctx_mod.set_host_bridge(self._old_bridge)

    def test_get_platform_by_name_str(self):
        """get_platform("aiocqhttp") 按平台类型名命中（对齐本体字符串匹配）。"""
        inst = self.context.get_platform("aiocqhttp")
        self.assertIsNotNone(inst)
        self.assertEqual(inst.meta().name, "aiocqhttp")
        self.assertEqual(inst.meta().id, "aiocqhttp_main")

    def test_get_platform_by_adapter_type_enum(self):
        """get_platform(PlatformAdapterType.AIOCQHTTP) 枚举位匹配（对齐本体）。"""
        from astrbot.core.star.filter.platform_adapter_type import PlatformAdapterType

        inst = self.context.get_platform(PlatformAdapterType.AIOCQHTTP)
        self.assertIsNotNone(inst)
        self.assertEqual(inst.meta().name, "aiocqhttp")
        # WEBCHAT 枚举位应命中 webchat 平台
        inst2 = self.context.get_platform(PlatformAdapterType.WEBCHAT)
        self.assertIsNotNone(inst2)
        self.assertEqual(inst2.meta().name, "webchat")

    def test_get_platform_not_found_returns_none(self):
        """未命中平台名返回 None（对齐本体），而非抛异常。"""
        self.assertIsNone(self.context.get_platform("telegram"))
        self.assertIsNone(self.context.get_platform(""))

    def test_get_platform_inst_by_id(self):
        """get_platform_inst(platform_id) 按平台实例 ID 命中（对齐本体）。"""
        inst = self.context.get_platform_inst("webchat_inst")
        self.assertIsNotNone(inst)
        self.assertEqual(inst.meta().id, "webchat_inst")
        # 与 get_platform 不同：aiocqhttp 的实例 ID 不是类型名
        self.assertIsNone(self.context.get_platform_inst("aiocqhttp"))

    def test_get_platform_inst_not_found_returns_none(self):
        self.assertIsNone(self.context.get_platform_inst("no_such_platform"))

    def test_platform_manager_is_platform_manager_class(self):
        """context.platform_manager 为 manager.PlatformManager 实例（类型对齐）。"""
        from astrbot.core.platform.manager import PlatformManager

        self.assertIsInstance(self.context.platform_manager, PlatformManager)
        # 平台清单经 ListPlatforms 惰性拉取可达
        self.assertEqual(len(self.context.platform_manager.get_insts()), 2)

    def test_get_platform_without_bridge_returns_none(self):
        """宿主桥缺失时不炸：get_platform 返回 None（插件可判空降级）。"""
        self.ctx_mod.set_host_bridge(None)
        fresh = self.ctx_mod.Context()
        self.assertIsNone(fresh.get_platform("aiocqhttp"))
        self.assertIsNone(fresh.get_platform_inst("aiocqhttp_main"))


class TestPlatformStubSendBySession(unittest.TestCase):
    """_PlatformStub.send_by_session 对齐本体 Platform.send_by_session。

    SDK 修复前平台占位实例缺少该方法——插件经 get_platform_inst(...) 拿到
    实例后调用 send_by_session(session, chain) 抛 AttributeError。
    """

    def test_send_by_session_delegates_to_host_bridge(self):
        import asyncio

        import astrbot.core.star.context as ctx_mod
        from astrbot.core.platform.message_session import MessageSession

        sent = []

        class _FakeSendBridge:
            def ensure_connected(self):
                return True

            def list_platforms(self):
                return [{"id": "p1", "type": "webchat", "name": "webchat"}]

            async def send_message_async(self, session, chain):
                sent.append((session.platform_id, session.session_id, chain))
                return True

        old_bridge = ctx_mod.get_host_bridge()
        ctx_mod.set_host_bridge(_FakeSendBridge())
        try:
            context = ctx_mod.Context()
            inst = context.get_platform_inst("p1")
            self.assertIsNotNone(inst)
            session = MessageSession(
                platform_name="p1", message_type=None, session_id="s1"
            )
            ok = asyncio.run(inst.send_by_session(session, ["chain"]))
            self.assertTrue(ok)
            self.assertEqual(sent[0][0], "p1")
            self.assertEqual(sent[0][1], "s1")
        finally:
            ctx_mod.set_host_bridge(old_bridge)


if __name__ == "__main__":
    unittest.main()
