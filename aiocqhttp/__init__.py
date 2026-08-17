"""
aiocqhttp 兼容层（OneBot v11 平台 · 宿主桥实现）。

对齐 Python aiocqhttp 库的常用编程面（装饰器事件循环 + call_api），但网络层
由宿主 Go 的 aiocqhttp 适配器承担：
- 事件循环由宿主驱动：宿主收到 OneBot 事件 → 经 on_message 桥接钩子把原始
  OneBot JSON 推给插件 → 本模块按 post_type 分发给 @bot.on_message / on_notice /
  on_request / on_meta_event / on_event 装饰器注册的 handler。
- API 调用经 HostBridge.call_action("aiocqhttp", action, params) 转发到宿主
  Go 适配器（与 Python AstrBot 里 aiocqhttp 直连 OneBot 的体验对齐：装饰器、
  Event 对象、call_api/send/api.* 均可用）。
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import threading
from typing import Any, Callable

from astrbot._bridge import loop
from astrbot._bridge.host import get_bridge

logger = logging.getLogger("aiocqhttp")

__all__ = ["CQHttp", "Event", "CQHttpConfig", "compat"]


class CQHttpConfig:
    """对齐 aiocqhttp.CQHttpConfig 的常用字段（宿主侧统一连接，无需网络参数）。"""

    def __init__(self, **kwargs):
        self.api_root = kwargs.get("api_root", "")
        self.api_host = kwargs.get("api_host", "")
        self.api_port = kwargs.get("api_port", 0)
        self.access_token = kwargs.get("access_token", "")
        self.secret = kwargs.get("secret", "")
        self.connection_retries = kwargs.get("connection_retries", 3)
        self.use_ws = kwargs.get("use_ws", True)
        # 其余自定义字段原样保留
        for k, v in kwargs.items():
            setattr(self, k, v)


class Event(dict):
    """OneBot v11 事件对象。属性访问即字典键访问（对齐 aiocqhttp.event.Event：
    self.__dict__ = self）。"""

    def __init__(self, data: dict | None = None):
        super().__init__(data or {})
        self.__dict__ = self

    @property
    def message(self) -> list[dict] | str:
        """原始 message 字段（CQ 段数组或 CQ 字符串）。"""
        return self.get("message", [])

    def get(self, key, default=None):  # noqa: A003
        return super().get(key, default)

    def __str__(self) -> str:
        return json.dumps(dict(self), ensure_ascii=False)


class _APIProxy:
    """bot.api.<action>(**params) → call_api(action, **params)。"""

    def __init__(self, bot: "CQHttp"):
        self._bot = bot

    def __getattr__(self, action: str) -> Callable:
        async def _call(**params) -> dict:
            return await self._bot.call_api(action, **params)

        return _call


class _Registry:
    """插件进程内 CQHttp 实例注册表（桥接钩子注入 + 事件分发用）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._instances: list[CQHttp] = []

    def register(self, bot: "CQHttp") -> None:
        with self._lock:
            if bot not in self._instances:
                self._instances.append(bot)

    def has_any(self) -> bool:
        with self._lock:
            return bool(self._instances)

    def instances(self) -> list["CQHttp"]:
        with self._lock:
            return list(self._instances)


_registry = _Registry()


def _run_handler(handler, event: Event) -> None:
    """同步/异步 handler 都在宿主事件循环里执行，异常不抛出桥接。"""
    try:
        result = handler(event)
        if inspect.iscoroutine(result):
            loop.run_coro(result)
        elif inspect.isasyncgen(result):
            async def _drain():
                async for _ in result:
                    pass

            loop.run_coro(_drain())
    except Exception as e:  # noqa: BLE001
        logger.error(f"aiocqhttp handler {getattr(handler, '__name__', handler)} 执行失败: {e}")


def dispatch(raw_event_json: str | bytes | dict | None) -> None:
    """把宿主推来的原始 OneBot 事件分发到各 CQHttp 实例的装饰器。

    宿主经 on_message 桥接钩子推送所有平台事件；只有 OneBot 原始事件
    （含 post_type）才会命中分发，其余平台事件被忽略。
    """
    if not _registry.has_any():
        return
    if raw_event_json is None:
        return
    try:
        if isinstance(raw_event_json, (str, bytes)):
            data = json.loads(raw_event_json)
        elif isinstance(raw_event_json, dict):
            data = raw_event_json
        else:
            return
    except (ValueError, TypeError):
        return
    if not isinstance(data, dict) or "post_type" not in data:
        return
    event = Event(data)
    post_type = data.get("post_type", "message")
    for bot in _registry.instances():
        for handler in bot._handlers_for(post_type, data):  # noqa: SLF001
            _run_handler(handler, event)


class CQHttp:
    """OneBot v11 机器人客户端（宿主桥兼容层）。

    装饰器：on_message / on_notice / on_request / on_meta_event / on_event /
    on_command / before_send / after_send。事件由宿主推送，run() 为 no-op。
    """

    def __init__(self, *args, **kwargs):
        self.config = CQHttpConfig(*args, **kwargs)
        self.api = _APIProxy(self)
        # event_type -> [handler]（message/notice/request/meta_event + 自定义）
        self._handlers: dict[str, list] = {"message": [], "notice": [], "request": [], "meta_event": []}
        # on_command：[(commands, handler)]
        self._command_handlers: list[tuple[list[str], Callable]] = []
        _registry.register(self)

    # ---- 装饰器 ----
    def on_message(self, func=None, *, event=None, command=None, **kwargs):
        return self._decorator("message", func, command=command)

    def on_notice(self, func=None, *, event=None, **kwargs):
        return self._decorator("notice", func, sub_event=event)

    def on_request(self, func=None, *, event=None, **kwargs):
        return self._decorator("request", func, sub_event=event)

    def on_meta_event(self, func=None, *, event=None, **kwargs):
        return self._decorator("meta_event", func, sub_event=event)

    def on_event(self, event_name: str):
        def deco(func):
            self._handlers.setdefault("custom", []).append((event_name, func))
            return func

        return deco

    def on_command(self, commands, *, aliases=None, **kwargs):
        """命令装饰器：消息首词命中 commands/aliases 时调用。"""
        names = list(commands) if isinstance(commands, (list, tuple)) else [commands]
        if aliases:
            names += list(aliases) if isinstance(aliases, (list, tuple)) else [aliases]

        def deco(func):
            self._command_handlers.append((names, func))
            return func

        return deco

    def before_send(self, func):
        return func

    def after_send(self, func):
        return func

    def _decorator(self, event_type: str, func, sub_event=None, command=None):
        if command is not None:
            # on_message(command="xxx") 形式 → 命令匹配
            return self.on_command(command)(func)
        if sub_event is not None:
            self._handlers.setdefault(event_type + "_" + sub_event, []).append(func)
            return func
        if func is None:
            # 支持 @bot.on_message(event="...") 带参形式（sub_event 由上层处理）
            return lambda f: self._handlers.setdefault(event_type, []).append(f) or f
        self._handlers[event_type].append(func)
        return func

    # ---- 事件匹配 ----
    def _handlers_for(self, post_type: str, data: dict) -> list:
        out = list(self._handlers.get(post_type, []))
        sub = data.get(post_type + "_type")
        if sub:
            out += list(self._handlers.get(f"{post_type}_{sub}", []))
        if post_type == "message":
            out += list(self._handlers.get("custom", []))
        # on_event(event_name) 的 custom 事件：按 event 名匹配
        return out

    # ---- API ----
    async def call_api(self, action: str, **params) -> dict:
        """调用 OneBot v11 API（经宿主 Go 适配器）。"""
        try:
            return await get_bridge().call_action_async("aiocqhttp", action, params)
        except Exception as e:  # noqa: BLE001
            logger.error(f"call_api {action} 失败: {e}")
            raise

    async def send(self, ctx, message, **kwargs) -> dict:
        """发送消息。ctx 可以是 Event（按 event 路由）或 dict(group_id=...)。"""
        if isinstance(ctx, Event) or (isinstance(ctx, dict) and "self_id" not in ctx):
            if isinstance(ctx, dict) and ("group_id" in ctx or "user_id" in ctx):
                params = dict(ctx)
                params["message"] = message
                if "group_id" in params:
                    return await self.call_api("send_group_msg", **params)
                return await self.call_api("send_private_msg", **params)
            if isinstance(ctx, Event):
                if ctx.get("group_id"):
                    return await self.call_api(
                        "send_group_msg", group_id=int(ctx["group_id"]), message=message
                    )
                if ctx.get("user_id"):
                    return await self.call_api(
                        "send_private_msg", user_id=int(ctx["user_id"]), message=message
                    )
        if isinstance(ctx, Event) and ctx.get("self_id"):
            return await self.call_api("send_msg", **{"self_id": ctx["self_id"], "message": message, **kwargs})
        raise ValueError(f"无法从 {ctx!r} 推断发送目标")

    # 常用便捷方法（其余 action 经 api.* / call_api 通用转发）
    async def call_action(self, action: str, **params) -> dict:
        """显式 call_action（= call_api）：真实 aiocqhttp 的 CQHttp 提供该方法，
        插件常以 `bot.call_action("get_login_info", **params)` 调用（位置参数
        形式）。缺了它会被 __getattr__ 误拦为动态 action，_call(**params) 收到
        位置参数报 "takes 0 positional arguments but 1 was given"。
        """
        return await self.call_api(action, **params)

    async def send_group_msg(self, **params) -> dict:
        return await self.call_api("send_group_msg", **params)

    async def send_private_msg(self, **params) -> dict:
        return await self.call_api("send_private_msg", **params)

    async def send_msg(self, **params) -> dict:
        return await self.call_api("send_msg", **params)

    async def delete_msg(self, **params) -> dict:
        return await self.call_api("delete_msg", **params)

    async def get_group_member_list(self, **params) -> dict:
        return await self.call_api("get_group_member_list", **params)

    async def get_group_info(self, **params) -> dict:
        return await self.call_api("get_group_info", **params)

    def __getattr__(self, action: str) -> Callable:
        """未显式定义的方法（get_group_list/set_group_leave/get_stranger_info
        等任意 OneBot action）→ 动态转发 call_api，与真实 aiocqhttp 行为一致
        （插件常直接调 client.<action>(...)）。"""
        if action.startswith("_") or action in ("call_api", "send", "run", "api", "config"):
            raise AttributeError(action)

        async def _call(**params) -> dict:
            return await self.call_api(action, **params)

        return _call

    # ---- 生命周期（事件由宿主驱动，均为 no-op 兼容） ----
    async def run(self, *args, **kwargs) -> None:
        logger.info("aiocqhttp 事件循环由宿主驱动，run() 无需调用")

    def run_asgi(self, *args, **kwargs) -> Any:
        return None

    @property
    def server_app(self):
        return None

    @property
    def context(self):
        return self


class compat:
    """aiocqhttp.compat 子模块常用符号（run_sync 等）。"""

    @staticmethod
    def run_sync(func, *args, **kwargs):
        result = func(*args, **kwargs)
        if inspect.iscoroutine(result):
            return loop.run_coro(result)
        return result
