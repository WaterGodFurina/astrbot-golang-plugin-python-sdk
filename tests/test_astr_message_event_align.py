"""AstrMessageEvent 本体语义对齐单测。

覆盖以下修复点（对齐本体 astrbot/core/platform/astr_message_event.py）：
- request_llm：同时传 contexts 与 conversation 时忽略 conversation
  （本体 :461-462）；
- stop_event：无 result 时创建 STOP 结果，event.get_result() 可拿到
  （本体 :343-349）；continue_event 对齐 CONTINUE（本体 :351-357）；
- get_message_type：优先 message_obj.type（本体 :184-189）；
- get_messages：message_obj.message 缺失时返回 []（本体 :180-182）；
- is_private_chat 基于 get_message_type()（本体 :255-257）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_event(message_type: str = "GroupMessage"):
    from astrbot.core.platform.astr_message_event import AstrMessageEvent
    from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
    from astrbot.core.platform.message_type import MessageType
    from astrbot.core.platform.platform_metadata import PlatformMetadata

    obj = AstrBotMessage()
    obj.type = MessageType(message_type)
    obj.self_id = "bot_1"
    obj.session_id = "session_1"
    obj.sender = MessageMember(user_id="u1", nickname="小明")
    obj.message = []
    obj.message_str = "/ping"
    meta = PlatformMetadata(name="aiocqhttp", description="", id="aiocqhttp_main")
    return AstrMessageEvent("/ping", obj, meta, "session_1")


class TestRequestLlmContextsConversationGuard(unittest.TestCase):
    def test_conversation_ignored_when_contexts_present(self):
        """contexts 非空时 conversation 被置 None（对齐本体）。"""
        event = _make_event()
        conv = object()
        req = event.request_llm(prompt="hi", contexts=[{"role": "user"}], conversation=conv)
        self.assertIsNone(req.conversation)

    def test_conversation_kept_without_contexts(self):
        """无 contexts 时 conversation 保留。"""
        event = _make_event()
        conv = object()
        req = event.request_llm(prompt="hi", conversation=conv)
        self.assertIs(req.conversation, conv)


class TestStopEventResult(unittest.TestCase):
    def test_stop_event_creates_stopped_result(self):
        """无 result 时 stop_event() 创建 STOP 结果（对齐本体 get_result 语义）。"""
        from astrbot.core.message.message_event_result import EventResultType

        event = _make_event()
        self.assertIsNone(event.get_result())
        event.stop_event()
        self.assertTrue(event.is_stopped())
        result = event.get_result()
        self.assertIsNotNone(result)
        self.assertTrue(result.is_stopped())
        self.assertEqual(result.result_type, EventResultType.STOP)

    def test_continue_event_clears_stop(self):
        """continue_event() 解除停止（对齐本体）。"""
        from astrbot.core.message.message_event_result import EventResultType

        event = _make_event()
        event.stop_event()
        event.continue_event()
        self.assertFalse(event.is_stopped())
        self.assertEqual(event.get_result().result_type, EventResultType.CONTINUE)


class TestMessageTypePreference(unittest.TestCase):
    def test_get_message_type_prefers_message_obj_type(self):
        """message_obj.type 变更后 get_message_type() 跟随（对齐本体优先级）。"""
        from astrbot.core.platform.message_type import MessageType

        event = _make_event("GroupMessage")
        self.assertEqual(event.get_message_type(), MessageType.GROUP_MESSAGE)
        event.message_obj.type = MessageType.FRIEND_MESSAGE
        self.assertEqual(event.get_message_type(), MessageType.FRIEND_MESSAGE)

    def test_is_private_chat_follows_message_obj_type(self):
        """is_private_chat 基于 get_message_type()（对齐本体）。"""
        from astrbot.core.platform.message_type import MessageType

        event = _make_event("GroupMessage")
        self.assertFalse(event.is_private_chat())
        event.message_obj.type = MessageType.FRIEND_MESSAGE
        self.assertTrue(event.is_private_chat())

    def test_get_messages_missing_chain_returns_empty(self):
        """message_obj.message 缺失时返回 []（对齐本体 getattr 兜底）。"""
        event = _make_event()
        del event.message_obj.message
        self.assertEqual(event.get_messages(), [])


if __name__ == "__main__":
    unittest.main()
