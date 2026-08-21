"""
python-telegram-bot 兼容层（宿主桥骨架）。

对齐插件常用的编程面（Bot 发送方法 + Update/Message 等类型对象 + 会话键
盘），网络层由宿主 Go 的 telegram 适配器承担：发送方法经
HostBridge.call_action("telegram", api, params) 转发宿主。装饰器事件循环
（Application/Updater.add_handler）注册面保留，事件分发机制与 aiocqhttp
兼容层相同（宿主 on_message 桥接钩子推送时按 Update 形状分发）。
"""
from __future__ import annotations

import inspect
import logging
import threading
from typing import Any

from astrbot._bridge import loop
from astrbot._bridge.host import get_bridge

logger = logging.getLogger("telegram")

TELEGRAM_BRIDGE_HOOK = "__telegram_bridge__"

__all__ = [
    "Bot",
    "Update",
    "Message",
    "Chat",
    "User",
    "ReplyKeyboardMarkup",
    "InlineKeyboardMarkup",
    "InlineKeyboardButton",
    "KeyboardButton",
    "Application",
    "Updater",
]


class User:
    """Telegram User 对象（常用字段）。"""

    def __init__(self, **kw):
        self.id = kw.get("id")
        self.is_bot = kw.get("is_bot", False)
        self.first_name = kw.get("first_name", "")
        self.last_name = kw.get("last_name", "")
        self.username = kw.get("username", "")
        self.language_code = kw.get("language_code", "")
        for k, v in kw.items():
            setattr(self, k, v)

    @property
    def full_name(self) -> str:
        return (self.first_name or "") + ((" " + self.last_name) if self.last_name else "")


class Chat:
    """Telegram Chat 对象。"""

    def __init__(self, **kw):
        self.id = kw.get("id")
        self.type = kw.get("type", "private")
        self.title = kw.get("title", "")
        self.username = kw.get("username", "")
        self.first_name = kw.get("first_name", "")
        for k, v in kw.items():
            setattr(self, k, v)


class Message:
    """Telegram Message 对象。"""

    def __init__(self, **kw):
        self.message_id = kw.get("message_id")
        self.chat = kw.get("chat")
        self.from_user = kw.get("from_user")
        self.text = kw.get("text", "")
        self.date = kw.get("date")
        for k, v in kw.items():
            setattr(self, k, v)

    @property
    def chat_id(self):
        return self.chat.id if isinstance(self.chat, Chat) else self.chat


class KeyboardButton:
    def __init__(self, text: str, **kw):
        self.text = text


class ReplyKeyboardMarkup:
    def __init__(self, keyboard, resize_keyboard=False, one_time_keyboard=False, **kw):
        self.keyboard = keyboard
        self.resize_keyboard = resize_keyboard
        self.one_time_keyboard = one_time_keyboard


class InlineKeyboardButton:
    def __init__(self, text: str, url=None, callback_data=None, **kw):
        self.text = text
        self.url = url
        self.callback_data = callback_data


class InlineKeyboardMarkup:
    def __init__(self, inline_keyboard, **kw):
        self.inline_keyboard = inline_keyboard


class Bot:
    """Telegram 机器人客户端（宿主桥）。"""

    def __init__(self, token: str = "", **kwargs):
        self.token = token
        self._handlers: list = []

    # ---- 发送方法（经宿主 Go telegram 适配器） ----
    async def send_message(self, chat_id, text: str, **params) -> Message:
        data = await get_bridge().call_action_async("telegram", "sendMessage", {
            "chat_id": chat_id, "text": text, **params,
        })
        return Message(**(data or {}))

    async def send_photo(self, chat_id, photo, caption: str = "", **params) -> Message:
        data = await get_bridge().call_action_async("telegram", "sendPhoto", {
            "chat_id": chat_id, "photo": photo, "caption": caption, **params,
        })
        return Message(**(data or {}))

    async def send_document(self, chat_id, document, caption: str = "", **params) -> Message:
        data = await get_bridge().call_action_async("telegram", "sendDocument", {
            "chat_id": chat_id, "document": document, "caption": caption, **params,
        })
        return Message(**(data or {}))

    async def send_audio(self, chat_id, audio, caption: str = "", **params) -> Message:
        data = await get_bridge().call_action_async("telegram", "sendAudio", {
            "chat_id": chat_id, "audio": audio, "caption": caption, **params,
        })
        return Message(**(data or {}))

    async def send_video(self, chat_id, video, caption: str = "", **params) -> Message:
        data = await get_bridge().call_action_async("telegram", "sendVideo", {
            "chat_id": chat_id, "video": video, "caption": caption, **params,
        })
        return Message(**(data or {}))

    async def send_sticker(self, chat_id, sticker, **params) -> Message:
        data = await get_bridge().call_action_async("telegram", "sendSticker", {
            "chat_id": chat_id, "sticker": sticker, **params,
        })
        return Message(**(data or {}))

    async def delete_message(self, chat_id, message_id, **params) -> bool:
        await get_bridge().call_action_async("telegram", "deleteMessage", {
            "chat_id": chat_id, "message_id": message_id, **params,
        })
        return True

    async def edit_message_text(self, text: str, chat_id=None, message_id=None, **params):
        body = {"text": text, **params}
        if chat_id is not None:
            body["chat_id"] = chat_id
        if message_id is not None:
            body["message_id"] = message_id
        return await get_bridge().call_action_async("telegram", "editMessageText", body)

    async def answer_callback_query(self, callback_query_id, text: str = "", **params) -> bool:
        await get_bridge().call_action_async("telegram", "answerCallbackQuery", {
            "callback_query_id": callback_query_id, "text": text, **params,
        })
        return True

    def __getattr__(self, method: str):
        """未显式实现的方法 → 动态转发 call_action。"""
        if method.startswith("_"):
            raise AttributeError(method)

        # snake_case → camelCase（send_location → sendLocation），对齐显式方法的 API 名
        parts = method.split("_")
        api = parts[0] + "".join(p.capitalize() for p in parts[1:])

        async def _call(**params):
            return await get_bridge().call_action_async("telegram", api, params)

        return _call

    async def get_me(self) -> User:
        data = await get_bridge().call_action_async("telegram", "getMe", {})
        return User(**(data or {}))


class Update:
    """Telegram Update 对象（消息/回调事件）。"""

    def __init__(self, **kw):
        self.update_id = kw.get("update_id")
        self.message = kw.get("message")
        self.edited_message = kw.get("edited_message")
        self.callback_query = kw.get("callback_query")
        for k, v in kw.items():
            setattr(self, k, v)


class _Registry:
    """插件进程内 Application 实例注册表（桥接钩子注入 + 事件分发用）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._instances: list[Application] = []

    def register(self, app: "Application") -> None:
        with self._lock:
            if app not in self._instances:
                self._instances.append(app)

    def has_any(self) -> bool:
        with self._lock:
            return bool(self._instances)

    def instances(self) -> list["Application"]:
        with self._lock:
            return list(self._instances)


_registry = _Registry()


class _Context:
    """极简 handler context：带 effective_chat / effective_user（对齐
    python-telegram-bot 的 CallbackContext 常用字段）。"""

    def __init__(self, update: Update, bot: Bot | None):
        self.update = update
        self.bot = bot
        self.effective_chat = update.message.chat if update and update.message else None
        self.effective_user = (
            update.message.from_user if update and update.message else None
        )


def _run_handler(handler, update: Update, app: "Application") -> None:
    """同步/异步 handler 都在宿主事件循环里执行，异常不抛出桥接。

    handler 支持两种形态：python-telegram-bot 风格的 MessageHandler（带
    .callback），或直接可调用对象。只处理消息事件（对齐原版逻辑）。
    """
    if not update.message:
        return
    callback = getattr(handler, "callback", None) or handler
    try:
        result = callback(update, _Context(update, app.bot))
        if inspect.iscoroutine(result):
            loop.run_coro(result)
    except Exception as e:  # noqa: BLE001
        logger.error(f"telegram handler {getattr(handler, '__name__', handler)} 执行失败: {e}")


def dispatch(event_data: dict) -> None:
    """把宿主推来的序列化 AstrMessageEvent 分发到各 Application 的 handler。

    event_data 即 HandleHook 收到的序列化 AstrMessageEvent。与原版 AstrBot
    逻辑对齐：只处理 update.message（不构造 callback_query）。无注册实例
    直接返回（宿主零额外开销）。
    """
    if not _registry.has_any():
        return
    if not isinstance(event_data, dict) or not event_data:
        return
    is_group = bool(event_data.get("is_group", False))
    message = Message(
        message_id=event_data.get("message_id"),
        text=event_data.get("plain_text") or event_data.get("message_str") or "",
        chat=Chat(
            id=str(event_data.get("conv_id", "")),
            type="group" if is_group else "private",
        ),
        from_user=User(
            id=event_data.get("sender_id"),
            first_name=event_data.get("sender_name", ""),
        ),
        date=event_data.get("timestamp", 0),
    )
    update = Update(update_id=0, message=message)
    for app in _registry.instances():
        for handler in list(app._handlers):
            _run_handler(handler, update, app)


class Application:
    """Application/Updater 骨架：add_handler 注册的 handler 由宿主驱动分发。

    事件循环由宿主驱动：宿主收到入站消息经 __telegram_bridge__ 桥接钩子把
    序列化 AstrMessageEvent 推给本插件，telegram.dispatch 重建 Update 后分发给
    add_handler 注册的 handler（对齐 python-telegram-bot：handler 形如
    MessageHandler(filters, callback)，兼容层按 handler.callback / handler.filters
    或直接可调用对象处理）。
    """

    def __init__(self, bot: Bot | None = None, **kw):
        self.bot = bot
        self._handlers: list = []
        _registry.register(self)
        # 向宿主注册桥接钩子：宿主收到入站消息时把序列化事件推给本插件的
        # HandleHook，再经 telegram.dispatch 分发到 handler。失败仅告警，
        # 不影响插件其它能力（普通调用路径零新增开销）。
        try:
            get_bridge().register_bridge_hook(TELEGRAM_BRIDGE_HOOK)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"telegram 桥接钩子注册失败: {e}")

    def add_handler(self, handler) -> None:
        self._handlers.append(handler)

    def add_error_handler(self, handler) -> None:
        import logging

        logging.getLogger("telegram").warning(
            "telegram Application.add_error_handler：事件分发暂不支持"
        )

    async def initialize(self) -> None:
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


class Updater(Application):
    pass
