"""
python-telegram-bot 兼容层（宿主桥骨架）。

对齐插件常用的编程面（Bot 发送方法 + Update/Message 等类型对象 + 会话键
盘），网络层由宿主 Go 的 telegram 适配器承担：发送方法经
HostBridge.call_action("telegram", api, params) 转发宿主。装饰器事件循环
（Application/Updater.add_handler）注册面保留，事件分发机制与 aiocqhttp
兼容层相同（宿主 on_message 桥接钩子推送时按 Update 形状分发）。
"""
from __future__ import annotations

from typing import Any

from astrbot._bridge.host import get_bridge

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

        async def _call(**params):
            return await get_bridge().call_action_async("telegram", method, params)

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


class Application:
    """Application/Updater 骨架：add_handler 保留注册面。

    注意：telegram 事件分发尚未接线（宿主 telegram 适配器暂不推送原始
    Update 事件），add_handler 注册的 handler 当前不会被执行——与
    aiocqhttp 兼容层的完整分发链路不同。插件请改用 Bot 的发送方法 +
    AstrBot 自身的命令/过滤器体系。
    """

    def __init__(self, bot: Bot | None = None, **kw):
        self.bot = bot
        self._handlers: list = []

    def add_handler(self, handler) -> None:
        import logging

        logging.getLogger("telegram").warning(
            "telegram Application.add_handler：事件分发暂不支持，handler 不会执行"
        )
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
