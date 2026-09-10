"""Reply 引用消息 proto 传输单测。

覆盖宿主 → 插件方向 Reply 组件的 proto 还原（OneBot 引用消息修复回归）：
- component_from_proto 完整还原 sender_id/sender_nickname/time/message_str/
  chain（对齐 Python 原版 aiocqhttp adapter 构造 Reply 的语义）；
- 嵌套 chain（Plain/Image）递归还原；
- 旧宿主/插件发送方向（无扩展字段）行为与原实现一致。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot._bridge.gen import plugin_pb2
from astrbot._bridge.serialize import component_from_proto, component_to_proto
from astrbot.core.message.components import Image, Plain, Reply


class TestReplyProto(unittest.TestCase):
    def test_reply_with_chain_and_sender(self):
        c = plugin_pb2.Component(
            type="Reply",
            id="r1",
            text="被引用文本",
            sender_id="10001",
            sender_name="阿明",
            sender_time=1788000123,
        )
        c.chain.add(type="Plain", text="被引用文本")
        c.chain.add(type="Image", url="https://example.com/q.png")

        r = component_from_proto(c)
        self.assertIsInstance(r, Reply)
        self.assertEqual(r.id, "r1")
        self.assertEqual(r.message_str, "被引用文本")
        self.assertEqual(r.sender_id, "10001")
        self.assertEqual(r.sender_nickname, "阿明")
        self.assertEqual(r.time, 1788000123)
        # deprecated 兼容字段对齐原版（text=message_str、qq=sender_id）
        self.assertEqual(r.text, "被引用文本")
        self.assertEqual(str(r.qq), "10001")
        # 嵌套被引用内容链
        self.assertEqual(len(r.chain), 2)
        self.assertIsInstance(r.chain[0], Plain)
        self.assertEqual(r.chain[0].text, "被引用文本")
        self.assertIsInstance(r.chain[1], Image)
        self.assertEqual(r.chain[1].url, "https://example.com/q.png")

    def test_reply_without_extension_fields(self):
        """旧宿主或插件发送方向：无 sender/chain 字段，行为与原实现一致。"""
        c = plugin_pb2.Component(type="Reply", id="r1", text="")
        r = component_from_proto(c)
        self.assertIsInstance(r, Reply)
        self.assertEqual(r.id, "r1")
        self.assertEqual(r.message_str, "")
        self.assertEqual(r.sender_id, "")
        self.assertEqual(r.sender_nickname, "")
        self.assertEqual(r.time, 0)
        self.assertEqual(r.chain, [])

    def test_reply_nested_chain(self):
        """嵌套引用（引用里再带引用）按深度递归还原。"""
        inner = plugin_pb2.Component(type="Reply", id="r0", text="内层")
        inner.chain.add(type="Plain", text="最内层")
        c = plugin_pb2.Component(type="Reply", id="r1", text="外层", sender_id="7")
        c.chain.add().CopyFrom(inner)
        c.chain.add(type="Plain", text="外层内容")

        r = component_from_proto(c)
        self.assertEqual(len(r.chain), 2)
        nested = r.chain[0]
        self.assertIsInstance(nested, Reply)
        self.assertEqual(nested.id, "r0")
        self.assertEqual(nested.message_str, "内层")
        self.assertEqual(len(nested.chain), 1)
        self.assertEqual(nested.chain[0].text, "最内层")
        self.assertEqual(r.chain[1].text, "外层内容")

    def test_reply_to_proto_send_direction_unchanged(self):
        """插件发送方向（component_to_proto）：Reply 仍只携带 id/text
        （text 为 deprecated 字段，插件显式设置时透传；chain/sender 不传）。"""
        reply = Reply(id="r9", message_str="被引用", text="被引用", sender_id="1", time=5, chain=[Plain(text="x")])
        pc = component_to_proto(reply)
        self.assertEqual(pc.type, "Reply")
        self.assertEqual(pc.id, "r9")
        self.assertEqual(pc.text, "被引用")
        self.assertEqual(len(pc.chain), 0)  # 发送方向不传被引用内容链
        self.assertEqual(pc.sender_id, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
