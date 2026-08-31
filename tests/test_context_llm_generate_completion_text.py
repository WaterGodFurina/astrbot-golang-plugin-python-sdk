"""Context.llm_generate 返回值 completion_text 对齐单测。

对齐本体：Context.llm_generate 返回 Provider.text_chat 的 LLMResponse，
其 completion_text 与 result_chain 均携带生成文本（插件常读
resp.completion_text，如 meme_manager 的 semantic_caption）。
SDK 修复前只填 result_chain、completion_text 恒为 None（静默错）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _FakeLLMBridge:
    """宿主桥桩：记录 chat_llm_async 参数并返回固定文本。"""

    def __init__(self, reply: str):
        self.reply = reply
        self.calls = []

    async def chat_llm_async(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.reply


class TestLlmGenerateCompletionText(unittest.TestCase):
    def setUp(self):
        import astrbot.core.star.context as ctx_mod

        self.ctx_mod = ctx_mod
        self.bridge = _FakeLLMBridge("生成的回复文本")
        self._old_bridge = ctx_mod.get_host_bridge()
        ctx_mod.set_host_bridge(self.bridge)
        self.context = ctx_mod.Context()

    def tearDown(self):
        self.ctx_mod.set_host_bridge(self._old_bridge)

    def _generate(self):
        import asyncio

        return asyncio.run(
            self.context.llm_generate(chat_provider_id="prov_1", prompt="hi")
        )

    def test_completion_text_is_set(self):
        """resp.completion_text 必须携带生成文本（修复前恒 None）。"""
        resp = self._generate()
        self.assertEqual(resp.completion_text, "生成的回复文本")

    def test_result_chain_carries_plain_text(self):
        """resp.result_chain 含同文本 Plain 组件（本体 result_chain 语义）。"""
        from astrbot.core.message.components import Plain

        resp = self._generate()
        self.assertEqual(len(resp.result_chain.chain), 1)
        self.assertIsInstance(resp.result_chain.chain[0], Plain)
        self.assertEqual(resp.result_chain.chain[0].text, "生成的回复文本")

    def test_provider_id_forwarded_to_host(self):
        """chat_provider_id 经 provider_id 参数转发宿主 ChatLLM。"""
        self._generate()
        args, kwargs = self.bridge.calls[0]
        self.assertEqual(kwargs.get("provider_id"), "prov_1")
        self.assertEqual(args[0], "hi")


if __name__ == "__main__":
    unittest.main()
