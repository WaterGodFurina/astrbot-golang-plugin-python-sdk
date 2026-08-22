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
    """OneBot v11 事件对象。

    对齐真实 aiocqhttp.event.Event：属性访问代理到字典键（__getattr__/
    __setattr__），并提供 type/detail_type/sub_type/name 派生属性。与
    早期实现不同，不用 `self.__dict__ = self`——那会引入自引用，导致
    pickle/deepcopy 失效且属性写入被 property 遮蔽。
    """

    def __init__(self, data: dict | None = None):
        super().__init__(data or {})

    @property
    def type(self) -> str:
        """事件类型：message / notice / request / meta_event。"""
        return self["post_type"]

    @property
    def detail_type(self) -> str:
        """事件具体类型（message_type / notice_type / request_type 等）。

        畸形事件（有 post_type 但缺 {post_type}_type 字段）时返回空串，
        避免 KeyError 崩溃。
        """
        return self.get(f"{self.type}_type", "")

    @property
    def sub_type(self) -> str | None:
        return self.get("sub_type")

    @property
    def name(self) -> str:
        """事件名：{type}.{detail_type}[.{sub_type}]。"""
        n = self.type + "." + self.detail_type
        if self.sub_type:
            n += "." + self.sub_type
        return n

    @property
    def message(self) -> list[dict] | str:
        """原始 message 字段（CQ 段数组或 CQ 字符串）；非消息事件为空列表。"""
        return self.get("message", [])

    def __getattr__(self, key):
        return self.get(key)

    def __setattr__(self, key, value) -> None:
        self[key] = value

    def get(self, key, default=None):  # noqa: A003
        return super().get(key, default)

    def __str__(self) -> str:
        return json.dumps(dict(self), ensure_ascii=False)

    def __repr__(self) -> str:
        return f"<Event, {dict.__repr__(self)}>"


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
            # aiocqhttp 生态的 handler 用 event.send() 回复，异步生成器的
            # yield 值没有对应的发送语义——只消费（驱动协程），不产出回复。
            async def _drain():
                async for yielded in result:
                    logger.debug(
                        "aiocqhttp handler %s yield 的值被忽略（请用 event.send() 回复）",
                        getattr(handler, "__name__", handler),
                    )

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
        logger.debug("aiocqhttp dispatch: 非 OneBot 事件（无 post_type）被忽略: %r", raw_event_json)
        return
    event = Event(data)
    post_type = data["post_type"]
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
        return self._decorator("message", func, sub_event=event, command=command)

    def on_notice(self, func=None, *, event=None, **kwargs):
        return self._decorator("notice", func, sub_event=event)

    def on_request(self, func=None, *, event=None, **kwargs):
        return self._decorator("request", func, sub_event=event)

    def on_meta_event(self, func=None, *, event=None, **kwargs):
        return self._decorator("meta_event", func, sub_event=event)

    def on_event(self, event_name: str):
        """自定义事件装饰器（本兼容层扩展，真实 aiocqhttp 无此 API）。

        event_name 按事件名匹配：既匹配元事件字段（data["event"]，如
        "lifecycle" / "heartbeat"），也匹配 "post_type.detail_type" 点分名
        （如 "meta_event.heartbeat"）。
        """

        def deco(func):
            self._handlers.setdefault("custom", []).append((event_name, func))
            return func

        return deco

    def on_command(self, commands, *, aliases=None, **kwargs):
        """命令装饰器：消息首词命中 commands/aliases 时调用。

        同时支持 @bot.on_command("x") 与 @bot.on_command("x")(func) 两种形式。
        """
        names = list(commands) if isinstance(commands, (list, tuple)) else [commands]
        if aliases:
            names += list(aliases) if isinstance(aliases, (list, tuple)) else [aliases]

        def wrap(func):
            self._command_handlers.append((names, func))
            return func

        def deco(func=None):
            if func is None:
                return wrap
            return wrap(func)

        return deco

    def before_send(self, func):
        """发送前钩子：宿主桥模式下为 no-op（消息直接由宿主转发）。

        返回原 func 以保持装饰器语义（插件可无副作用地叠加该装饰器）。
        """
        logger.debug("before_send 在宿主桥模式下为 no-op，已忽略 %r", func)
        return func

    def after_send(self, func):
        """发送后钩子：宿主桥模式下为 no-op（消息直接由宿主转发）。

        返回原 func 以保持装饰器语义（插件可无副作用地叠加该装饰器）。
        """
        logger.debug("after_send 在宿主桥模式下为 no-op，已忽略 %r", func)
        return func

    def _decorator(self, event_type: str, func, sub_event=None, command=None):
        if command is not None:
            # on_message(command="xxx") 形式 → 命令匹配（func 可能为 None）
            return self.on_command(command)(func)
        if func is None:
            # @bot.on_message() / @bot.on_message(event="group") 带参形式：
            # 返回可用的装饰器，sub_event 过滤在注册时落位。
            def deco(f):
                if sub_event is not None:
                    self._handlers.setdefault(event_type + "_" + sub_event, []).append(f)
                else:
                    self._handlers.setdefault(event_type, []).append(f)
                return f

            return deco
        if sub_event is not None:
            self._handlers.setdefault(event_type + "_" + sub_event, []).append(func)
            return func
        self._handlers[event_type].append(func)
        return func

    # ---- 事件匹配 ----
    def _handlers_for(self, post_type: str, data: dict) -> list:
        out = list(self._handlers.get(post_type, []))
        sub = data.get(post_type + "_type")
        if sub:
            out += list(self._handlers.get(f"{post_type}_{sub}", []))
        if post_type == "message":
            # on_command 命令匹配
            if self._command_handlers and "message" in data:
                text = data["message"]
                # message 为 None 时 str(None) 会变成 "None" 可能误匹配命令，
                # 故对 None / 非 str / 非 list 直接跳过命令匹配。
                if not isinstance(text, (str, list)):
                    text = ""
                if isinstance(text, list):
                    text = "".join(
                        seg.get("data", {}).get("text", "")
                        for seg in text
                        if isinstance(seg, dict) and seg.get("type") == "text"
                    )
                first = str(text).split(maxsplit=1)[0] if str(text).strip() else ""
                # 迭代前拷贝：避免 on_message 等回调中增删 handler 导致迭代
                # 中列表变化（RuntimeError: list changed size during iteration）
                for names, func in list(self._command_handlers):
                    if first in names:
                        out.append(func)
        # on_event(event_name) 的 custom 事件：按事件名匹配（data["event"]
        # 元事件字段或 "post_type.detail_type" 点分名），未命中不调用。
        dotted = f"{post_type}.{data.get(post_type + '_type', '')}"
        for name, func in list(self._handlers.get("custom", [])):
            if name == data.get("event") or name == dotted or name == post_type:
                out.append(func)
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
        """发送消息。ctx 可以是 Event（按事件路由）或 dict(group_id=...)。

        对齐真实 aiocqhttp：从事件/字典的 group_id / user_id 推断发送目标。
        额外支持 self_id 多账号路由：若 ctx 带 self_id（Event 或 dict），
        则一并传给宿主，由宿主据此路由到对应账号（对齐参考实现
        aiocqhttp_message_event.py 的 routing_params 行为）。其余 kwargs
        原样透传给对应 action。
        """
        routing_params = {}
        if isinstance(ctx, dict) and ctx.get("self_id"):
            routing_params["self_id"] = ctx["self_id"]
        if isinstance(ctx, Event):
            # Event 优先（Event 是 dict 子类，若先查 dict 会把 post_type 等
            # 事件字段一并带进 API 参数）
            if not routing_params and ctx.get("self_id"):
                routing_params["self_id"] = ctx["self_id"]
            gid = ctx.get("group_id")
            uid = ctx.get("user_id")
            if gid:
                gid_int = _try_int(gid)
                if gid_int is None:
                    # 非数字 group_id（畸形数据）→ 走 send_msg 兜底分支，
                    # 避免 int 转换抛 ValueError 导致插件侧崩溃无信息
                    return await self.call_api(
                        "send_msg",
                        message=message,
                        **routing_params,
                        **kwargs,
                    )
                return await self.call_api(
                    "send_group_msg",
                    group_id=gid_int,
                    message=message,
                    **routing_params,
                    **kwargs,
                )
            if uid:
                uid_int = _try_int(uid)
                if uid_int is None:
                    return await self.call_api(
                        "send_msg",
                        message=message,
                        **routing_params,
                        **kwargs,
                    )
                return await self.call_api(
                    "send_private_msg",
                    user_id=uid_int,
                    message=message,
                    **routing_params,
                    **kwargs,
                )
        if isinstance(ctx, dict) and ("group_id" in ctx or "user_id" in ctx):
            # 与 Event 分支一致：self_id 走 routing_params 单独传递，不从
            # dict 原样透传（避免把事件字段带进 action 参数）。
            params = {k: v for k, v in ctx.items() if k != "self_id"}
            params["message"] = message
            params.update(kwargs)
            if routing_params:
                params.update(routing_params)
            if "group_id" in params:
                return await self.call_api("send_group_msg", **params)
            return await self.call_api("send_private_msg", **params)
        raise ValueError(
            f"无法从 {ctx!r} 推断发送目标：需要 group_id 或 user_id（事件或 dict）"
        )

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

    @staticmethod
    def get_default_bot() -> "CQHttp":
        """返回进程内首个注册的 CQHttp 实例；无则懒创建。

        宿主事件重建（from_event_json 注入 event.bot）必须复用同一个实例，
        否则 _registry 无限增长且事件被重复分发。
        """
        for bot in _registry.instances():
            return bot
        bot = CQHttp()
        return bot


class compat:
    """aiocqhttp.compat 子模块常用符号（run_sync 等）。"""

    @staticmethod
    def run_sync(func, *args, **kwargs):
        result = func(*args, **kwargs)
        if inspect.iscoroutine(result):
            try:
                return loop.run_coro(result)
            except RuntimeError as e:
                # 事件循环未就绪/已关闭时 run_coro 会抛 RuntimeError；捕获并
                # 记录日志后 re-raise，避免插件侧只见裸异常无上下文。
                logger.error(f"compat.run_sync 在事件循环不可用的情况下执行协程失败: {e}")
                raise
        if inspect.isasyncgen(result):
            # 异步生成器：消费并把 yield 值收集为列表返回
            async def _collect():
                return [item async for item in result]

            try:
                return loop.run_coro(_collect())
            except RuntimeError as e:
                logger.error(f"compat.run_sync 在事件循环不可用的情况下消费异步生成器失败: {e}")
                raise
        return result


def _try_int(value) -> int | None:
    """OneBot ID 字段（group_id/user_id）宽松转 int：int/str 可转则转，
    否则返回 None（对齐参考实现 aiocqhttp_message_event.py 的 isdigit
    兜底语义，供 send() 在畸形 ID 时走 send_msg 兜底分支）。"""
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
