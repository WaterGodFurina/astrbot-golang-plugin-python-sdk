"""
botpy 兼容层（QQ 官方开放平台 SDK · 宿主桥骨架）。

对齐插件常用的编程面：Client/QQBot 构造、Message 等类型对象、发送方法
（MessageSend/TextMessage 等）。网络层由宿主 Go 的 qq_official 适配器承担：
发送经 HostBridge.call_action("qq_official", api, params) 转发宿主。
"""
from __future__ import annotations

from astrbot._bridge.host import get_bridge

__all__ = [
    "Client",
    "Bot",
    "QQBot",
    "Message",
    "MessageReference",
    "TextMessage",
    "MessageSend",
    "Member",
    "Group",
]


class Message:
    """botpy Message 对象（常用字段）。"""

    def __init__(self, **kw):
        self.id = kw.get("id")
        self.channel_id = kw.get("channel_id")
        self.guild_id = kw.get("guild_id")
        self.content = kw.get("content", "")
        self.author = kw.get("author")
        self.timestamp = kw.get("timestamp")
        for k, v in kw.items():
            setattr(self, k, v)


class MessageReference:
    def __init__(self, message_id: str = "", **kw):
        self.message_id = message_id
        for k, v in kw.items():
            setattr(self, k, v)


class TextMessage:
    """文本消息（MessageSend.content 常用形态）。"""

    def __init__(self, content: str = ""):
        self.content = content
        self.message_type = 0

    def to_dict(self) -> dict:
        return {"content": self.content, "message_type": self.message_type}


class MessageSend:
    """发送消息参数（对齐 botpy.message.MessageSend）。"""

    def __init__(self, content=None, msg_type: int = 0, msg_id: str = "", **kw):
        self.content = content if isinstance(content, str) else (content.to_dict() if content else None)
        self.msg_type = msg_type
        self.msg_id = msg_id
        for k, v in kw.items():
            setattr(self, k, v)

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "msg_type": self.msg_type,
            "msg_id": self.msg_id,
        }


class Member:
    def __init__(self, **kw):
        self.user_id = kw.get("user_id")
        self.nick = kw.get("nick", "")
        self.username = kw.get("username", "")
        for k, v in kw.items():
            setattr(self, k, v)


class Group:
    def __init__(self, **kw):
        self.group_id = kw.get("group_id")
        self.group_name = kw.get("group_name", "")
        for k, v in kw.items():
            setattr(self, k, v)


class Client:
    """QQ 官方机器人客户端（宿主桥）。intents 等参数仅作兼容保留。"""

    def __init__(self, intents=None, is_sandbox: bool = False, **kwargs):
        self.intents = intents
        self.is_sandbox = is_sandbox
        self.api = _APIRegistry(self)
        self._handlers: list = []

    # ---- 发送（channel 场景） ----
    async def post_message(self, channel_id: str, message: MessageSend | str) -> Message:
        payload = message.to_dict() if isinstance(message, MessageSend) else {"content": str(message)}
        data = await get_bridge().call_action_async("qq_official", "post_message", {
            "channel_id": channel_id, **payload,
        })
        return Message(**(data or {}))

    # ---- 事件注册（装饰器：on_message/on_at_message/on_member 等） ----
    def on_message(self, func=None, **kw):
        return self._on("message", func)

    def on_at_message(self, func=None, **kw):
        return self._on("at_message", func)

    def on_direct_message(self, func=None, **kw):
        return self._on("direct_message", func)

    def on_member_join(self, func=None, **kw):
        return self._on("member_join", func)

    def on_member_leave(self, func=None, **kw):
        return self._on("member_leave", func)

    def _on(self, event_type, func):
        import logging

        logging.getLogger("botpy").warning(
            f"botpy 事件分发暂不支持（宿主 qq_official 适配器不推送原始事件），"
            f"@on_{event_type} 注册的 handler 不会执行"
        )
        if func is None:
            return lambda f: self._handlers.append((event_type, f)) or f
        self._handlers.append((event_type, func))
        return func

    async def run(self, appid: str = "", token: str = "", **kw) -> None:
        # 事件由宿主驱动（与 aiocqhttp 兼容层同机制）；当前 botpy 事件分发
        # 未接线，装饰器注册的 handler 不会被执行（见 _on 的 warning）。
        pass


class Bot(Client):
    pass


class QQBot(Client):
    pass


class _APIRegistry:
    """client.api 命名空间（botpy 风格 client.api.xxx 调用）。"""

    def __init__(self, owner):
        self._owner = owner

    def __getattr__(self, name):
        async def _call(**params):
            return await get_bridge().call_action_async("qq_official", name, params)

        return _call
