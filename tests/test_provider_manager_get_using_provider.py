"""ProviderManager.get_using_provider(provider_type=...) 按名传参对齐本体签名。

本体第一参数名是 provider_type（astrbot-py manager.py:281-285），SDK 原用
capability，按名传参会 TypeError；修复后同时兼容枚举与能力字符串。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot.core.provider.entities import ProviderType
from astrbot.core.provider.manager import ProviderManager


def _fake_bridge(payload):
    class FakeBridge:
        def get_using_provider(self, umo: str = "", capability: str = ""):
            self.seen = (umo, capability)
            return payload

    return FakeBridge()


_PAYLOAD = {
    "id": "p1",
    "model": "gpt-x",
    "type": "chat_completion",
    "provider_type": "chat_completion",
}


class TestGetUsingProviderByName(unittest.TestCase):
    def test_provider_type_enum_kwarg(self):
        """get_using_provider(provider_type=ProviderType.CHAT_COMPLETION) 不炸。"""
        bridge = _fake_bridge(_PAYLOAD)
        mgr = ProviderManager(bridge=bridge)
        inst = mgr.get_using_provider(
            provider_type=ProviderType.CHAT_COMPLETION, umo="u1"
        )
        self.assertIsNotNone(inst)
        self.assertEqual(inst.meta_id, "p1")
        # 枚举已归一化为宿主能力字符串
        self.assertEqual(bridge.seen, ("u1", "chat_completion"))
        # 结果缓存到 curr_provider_inst（对齐本体属性）
        self.assertIs(mgr.curr_provider_inst, inst)

    def test_capability_string_still_supported(self):
        """宿主能力字符串位置传参继续可用（SDK 原有用法不回归）。"""
        bridge = _fake_bridge(_PAYLOAD)
        mgr = ProviderManager(bridge=bridge)
        inst = mgr.get_using_provider("chat_completion")
        self.assertEqual(inst.meta_id, "p1")

    def test_async_version_provider_type_kwarg(self):
        """get_using_provider_async(provider_type=...) 同样对齐（mock 异步桥）。"""
        payload = dict(_PAYLOAD, provider_type="text_to_speech")

        class FakeAsyncBridge:
            async def get_using_provider_async(self, umo="", capability=""):
                self.seen = (umo, capability)
                return payload

        bridge = FakeAsyncBridge()
        mgr = ProviderManager(bridge=bridge)
        import asyncio

        inst = asyncio.run(
            mgr.get_using_provider_async(
                provider_type=ProviderType.TEXT_TO_SPEECH, umo="u2"
            )
        )
        self.assertIsNotNone(inst)
        self.assertEqual(bridge.seen, ("u2", "text_to_speech"))
        self.assertIs(mgr.curr_tts_provider_inst, inst)


class TestManagerConfigHelpers(unittest.TestCase):
    def test_get_provider_config_by_id(self):
        """get_provider_config_by_id 命中/未命中（签名对齐本体 merged kwarg）。"""
        bridge = _fake_bridge(None)

        class ListBridge(_fake_bridge(None).__class__):
            def list_providers(self, capability=""):
                return [dict(_PAYLOAD)]

        mgr = ProviderManager(bridge=ListBridge())
        cfg = mgr.get_provider_config_by_id("p1", merged=True)
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["id"], "p1")
        self.assertIsNone(mgr.get_provider_config_by_id("nope"))

    def test_get_merged_provider_config_returns_copy(self):
        """get_merged_provider_config 返回深拷贝（无配置源概念，语义对齐）。"""
        mgr = ProviderManager(bridge=_fake_bridge(None))
        cfg = {"id": "p1", "model": "m"}
        out = mgr.get_merged_provider_config(cfg)
        self.assertEqual(out, cfg)
        self.assertIsNot(out, cfg)
        out["model"] = "changed"
        self.assertEqual(cfg["model"], "m")

    def test_dynamic_import_provider_noop(self):
        """dynamic_import_provider 薄壳 no-op 不抛错。"""
        mgr = ProviderManager(bridge=_fake_bridge(None))
        mgr.dynamic_import_provider("openai_chat_completion")


if __name__ == "__main__":
    unittest.main()
