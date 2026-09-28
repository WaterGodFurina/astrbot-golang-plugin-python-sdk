"""覆盖 aiocqhttp 兼容层的平台实例 ID 绑定与 on_websocket_connection 修复。

回归点：
- CQHttp.call_api 不能写死平台类型名，事件构造时用 for_platform(实例ID) 绑定。
- for_platform 不注册进全局表、共享 handler（避免注册表增长 / 重复分发）。
- on_websocket_connection 是同步装饰器，避免 @client.on_* 产生未 await 协程。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestCQHttpForPlatform(unittest.TestCase):
    def test_for_platform_binds_id_and_shares_handlers(self):
        from aiocqhttp import CQHttp, _registry

        bot = CQHttp()
        before = len(_registry.instances())
        clone = bot.for_platform("default")
        # 绑定实例 ID
        self.assertEqual(clone._platform_id, "default")
        # 共享 handler 容器（装饰器注册对全局可见）
        self.assertIs(clone._handlers, bot._handlers)
        # 不注册进全局表（避免注册表增长 / 重复分发）
        self.assertEqual(len(_registry.instances()), before)
        self.assertNotIn(clone, _registry.instances())

    def test_on_websocket_connection_is_sync_decorator(self):
        from aiocqhttp import CQHttp

        bot = CQHttp()
        self.assertFalse(bot._ws_poll_started)

        async def handler(event):
            return event.self_id

        ret = bot.on_websocket_connection(handler)
        # 同步装饰器：返回原函数，绝不产生未 await 的协程
        self.assertIs(ret, handler)
        self.assertEqual(bot._ws_callbacks, [handler])


class TestPlatformBotProxyDecorators(unittest.TestCase):
    def test_on_websocket_connection_is_sync(self):
        from astrbot.core.star.context import _PlatformBotProxy

        proxy = _PlatformBotProxy("default")
        self.assertFalse(proxy._ws_poll_started)

        async def handler(event):
            return event.self_id

        ret = proxy.on_websocket_connection(handler)
        self.assertIs(ret, handler)
        self.assertEqual(proxy._ws_callbacks, [handler])

    def test_unknown_on_decorator_does_not_return_coroutine(self):
        from astrbot.core.star.context import _PlatformBotProxy

        proxy = _PlatformBotProxy("default")
        deco = proxy.on_unknown_event
        self.assertTrue(callable(deco))

        async def handler(event):
            return None

        ret = deco(handler)
        # 未知事件装饰器返回原函数（同步），而非未 await 的协程
        self.assertIs(ret, handler)


if __name__ == "__main__":
    unittest.main()
