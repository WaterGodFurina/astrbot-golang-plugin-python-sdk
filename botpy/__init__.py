"""
botpy 兼容层（QQ 官方开放平台 SDK · 宿主桥实现）。

对齐插件常用的编程面：Client/QQBot 构造、Message 等类型对象、发送方法
（MessageSend/TextMessage 等）。网络层由宿主 Go 的 qq_official 适配器承担：
发送经 HostBridge.call_action("qq_official", api, params) 转发宿主。

事件循环由宿主驱动：宿主收到入站消息 → 经 __botpy_bridge__ 桥接钩子把序列化
AstrMessageEvent 推给本插件 → 本模块重建 botpy Message 并按事件类型分发给
@on_message / @on_at_message / @on_direct_message 等装饰器注册的 handler。
"""
from __future__ import annotations

import inspect
import logging
import threading

from astrbot._bridge import loop
from astrbot._bridge.host import get_bridge

logger = logging.getLogger("botpy")

BOTPY_BRIDGE_HOOK = "__botpy_bridge__"

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
        if content is None:
            self.content = ""
        elif isinstance(content, str):
            self.content = content
        elif hasattr(content, "content"):
            self.content = str(content.content)
        elif hasattr(content, "to_dict"):
            self.content = str(content.to_dict().get("content", ""))
        else:
            self.content = str(content)
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


class _Registry:
    """插件进程内 Client 实例注册表（桥接钩子注入 + 事件分发用）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._instances: list[Client] = []

    def register(self, client: "Client") -> None:
        with self._lock:
            if client not in self._instances:
                self._instances.append(client)

    def has_any(self) -> bool:
        with self._lock:
            return bool(self._instances)

    def instances(self) -> list["Client"]:
        with self._lock:
            return list(self._instances)


_registry = _Registry()


def _run_handler(handler, message: Message) -> None:
    """同步/异步 handler 都在宿主事件循环里执行，异常不抛出桥接。"""
    try:
        result = handler(message)
        if inspect.iscoroutine(result):
            loop.run_coro(result)
    except Exception as e:  # noqa: BLE001
        logger.error(f"botpy handler {getattr(handler, '__name__', handler)} 执行失败: {e}")


def dispatch(event_data: dict) -> None:
    """把宿主推来的序列化 AstrMessageEvent 分发到各 Client 实例的装饰器。

    event_data 即 HandleHook 收到的序列化 AstrMessageEvent（含 message_type /
    sender_id / sender_name / conv_id / is_group / is_at_bot / plain_text /
    message_id 等）。无注册实例直接返回（宿主零额外开销）。
    """
    if not _registry.has_any():
        return
    if not isinstance(event_data, dict) or not event_data:
        return
    msg_type = event_data.get("message_type", "")
    is_group = bool(event_data.get("is_group", False))
    try:
        from astrbot.core.platform.message_type import MessageType

        try:
            mtype = MessageType(msg_type) if msg_type else (
                MessageType.GROUP_MESSAGE if is_group else MessageType.FRIEND_MESSAGE
            )
        except ValueError:
            mtype = MessageType.GROUP_MESSAGE if is_group else MessageType.FRIEND_MESSAGE
    except Exception:  # noqa: BLE001
        mtype = None
    is_friend = mtype == MessageType.FRIEND_MESSAGE if mtype is not None else not is_group
    is_group_msg = mtype == MessageType.GROUP_MESSAGE if mtype is not None else is_group
    is_at = bool(event_data.get("is_at_bot", False))

    message = Message(
        id=str(event_data.get("message_id", "")),
        content=event_data.get("plain_text") or event_data.get("message_str") or "",
        author=Member(
            user_id=str(event_data.get("sender_id", "")),
            nick=event_data.get("sender_name", ""),
        ),
        channel_id=str(event_data.get("conv_id", "")),
        guild_id=str(event_data.get("conv_id", "")) if is_group_msg else "",
        timestamp=event_data.get("timestamp", 0),
    )

    for client in _registry.instances():
        for event_type, handler in list(client._handlers):
            if _botpy_event_matches(event_type, is_group_msg, is_friend, is_at):
                _run_handler(handler, message)


def _botpy_event_matches(event_type: str, is_group: bool, is_friend: bool, is_at: bool) -> bool:
    """botpy 装饰器事件类型 → 宿主事件匹配。

    简化对齐：on_message 收所有消息；on_at_message 收 IsAtBot=True 的群消息；
    on_direct_message 收 FriendMessage；on_member_join/leave 无宿主数据不触发
    （保持注册但不执行）。
    """
    if event_type == "message":
        return True
    if event_type == "at_message":
        return is_group and is_at
    if event_type == "direct_message":
        return is_friend
    return False


class Client:
    """QQ 官方机器人客户端（宿主桥）。intents 等参数仅作兼容保留。"""

    def __init__(self, intents=None, is_sandbox: bool = False, **kwargs):
        self.intents = intents
        self.is_sandbox = is_sandbox
        self.api = _APIRegistry(self)
        self._handlers: list = []
        _registry.register(self)
        # 向宿主注册桥接钩子：宿主收到入站消息时把序列化事件推给本插件的
        # HandleHook，再经 botpy.dispatch 分发到装饰器 handler。失败仅告警，
        # 不影响插件其它能力（普通调用路径零新增开销）。
        try:
            get_bridge().register_bridge_hook(BOTPY_BRIDGE_HOOK)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"botpy 桥接钩子注册失败: {e}")

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
        if func is None:
            return lambda f: self._handlers.append((event_type, f)) or f
        self._handlers.append((event_type, func))
        return func

    async def run(self, appid: str = "", token: str = "", **kw) -> None:
        # 事件由宿主驱动（与 aiocqhttp 兼容层同机制）：宿主收到入站消息经
        # __botpy_bridge__ 钩子推给本插件，dispatch 再分发到装饰器 handler。
        # 此处为 no-op 兼容。
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
