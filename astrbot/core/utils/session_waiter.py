"""会话控制（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.utils.session_waiter` 的完整实现：
SessionWaiter 等待用户下一条消息，期间由会话过滤器（SessionFilter）界定
“哪些消息属于这个会话”，收到后交给注册的 handler 处理。

**跨进程喂入机制**：register_wait 时经宿主桥 HostService.RegisterSessionWait
向宿主注册“等待该 umo（session_id）的下一条消息”；宿主收到该 umo 的消息
时经 PluginService.FeedSessionWait 推送事件，dispatch 反序列化后调用
`try_trigger(event)`：遍历 USER_SESSIONS，用各 waiter 的
session_filter.filter(event) 生成 session_id 比对 waiter.session_id，匹配则
触发该会话（与本体 star_manager 遍历 FILTERS 匹配的语义对齐）。

**降级说明**：
- 宿主不支持会话等待（RegisterSessionWait 返回 "" / RPC 失败）时，注册
  逻辑静默失败并降级为纯本地等待（现状行为：挂起至超时抛 TimeoutError，
  或由插件自行调用 `SessionWaiter.trigger(session_id, event)` 喂入）；
- 等待结束（正常/异常/超时）时经 _cleanup 注销宿主侧的等待（幂等，
  宿主侧超时 AfterFunc 已自动注销时静默）。

FILTERS / USER_SESSIONS 为模块级全局状态，插件可读写（常见用法：
`FILTERS.append(selection_filter)` 后自行管理移除）。
"""
import abc
import asyncio
import copy
import functools
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from astrbot.core.platform import AstrMessageEvent

logger = logging.getLogger("astrbot.session_waiter")

USER_SESSIONS: dict[str, "SessionWaiter"] = {}  # 存储 SessionWaiter 实例
FILTERS: list["SessionFilter"] = []  # 存储 SessionFilter 实例


class SessionController:
    """控制一个 Session 是否已经结束"""

    def __init__(self) -> None:
        self.future = asyncio.Future()
        self.current_event: asyncio.Event | None = None
        """当前正在等待的所用的异步事件"""
        self.ts: float | None = None
        """上次保持(keep)开始时的时间"""
        self.timeout: float | int | None = None
        """上次保持(keep)开始时的超时时间"""

        self.history_chains: list[list[Any]] = []

    def stop(self, error: Exception | None = None) -> None:
        """立即结束这个会话"""
        if not self.future.done():
            if error:
                self.future.set_exception(error)
            else:
                self.future.set_result(None)

    def keep(self, timeout: float = 0, reset_timeout=False) -> None:
        """保持这个会话

        Args:
            timeout (float): 必填。会话超时时间。
            当 reset_timeout 设置为 True 时, 代表重置超时时间, timeout 必须 > 0, 如果 <= 0 则立即结束会话。
            当 reset_timeout 设置为 False 时, 代表继续维持原来的超时时间, 新 timeout = 原来剩余的timeout + timeout (可以 < 0)

        """
        new_ts = time.time()

        if reset_timeout:
            if timeout <= 0:
                self.stop()
                return
        else:
            assert self.timeout is not None
            assert self.ts is not None
            left_timeout = self.timeout - (new_ts - self.ts)
            timeout = left_timeout + timeout
            if timeout <= 0:
                self.stop()
                return

        if self.current_event and not self.current_event.is_set():
            self.current_event.set()  # 通知上一个 keep 结束

        new_event = asyncio.Event()
        self.ts = new_ts
        self.current_event = new_event
        self.timeout = timeout

        asyncio.create_task(self._holding(new_event, timeout))  # 开始新的 keep

    async def _holding(self, event: asyncio.Event, timeout: float) -> None:
        """等待事件结束或超时"""
        try:
            await asyncio.wait_for(event.wait(), timeout)
        except asyncio.TimeoutError:
            if not self.future.done():
                self.future.set_exception(TimeoutError("等待超时"))
        except asyncio.CancelledError:
            pass  # 避免报错
        # finally:

    def get_history_chains(self) -> list[list[Any]]:
        """获取历史消息链"""
        return self.history_chains


class SessionFilter:
    """如何界定一个会话"""

    @abc.abstractmethod
    def filter(self, event: AstrMessageEvent) -> str:
        """根据事件返回一个会话标识符"""


class DefaultSessionFilter(SessionFilter):
    def filter(self, event: AstrMessageEvent) -> str:
        """默认实现，返回统一消息来源字符串作为会话标识符"""
        return event.unified_msg_origin


class SessionWaiter:
    def __init__(
        self,
        session_filter: SessionFilter,
        session_id: str,
        record_history_chains: bool,
    ) -> None:
        self.session_id = session_id
        self.session_filter = session_filter
        self.handler: (
            Callable[[SessionController, AstrMessageEvent], Awaitable[Any]] | None
        ) = None  # 处理函数

        self.session_controller = SessionController()
        self.record_history_chains = record_history_chains
        """是否记录历史消息链"""

        self.wait_id: str = ""
        """宿主侧注册的等待 ID（RegisterSessionWait 返回值；注册失败为
        "" 表示未注册，_cleanup 时跳过注销）。"""

        self._lock = asyncio.Lock()
        """需要保证一个 session 同时只有一个 trigger"""

    async def _register_host_wait(self, timeout: int) -> None:
        """向宿主注册跨进程喂入（session_id 即宿主匹配消息用的 umo）。

        宿主不支持（返回 ""）或 RPC 失败时静默降级为纯本地等待；wait_id
        留空，_cleanup 不再注销。延迟 import context 避免循环依赖。
        """
        try:
            from astrbot.core.star.context import get_host_bridge

            bridge = get_host_bridge()
            if bridge is None:
                return
            self.wait_id = await bridge.register_session_wait_async(
                umo=self.session_id, timeout_seconds=timeout
            )
        except Exception as e:
            # 宿主桥未就绪 / 旧宿主无此 RPC 等：降级为纯本地等待（现状行为）
            logger.debug(f"RegisterSessionWait 失败，降级为本地等待: {e}")
            self.wait_id = ""

    async def register_wait(
        self,
        handler: Callable[[SessionController, AstrMessageEvent], Awaitable[Any]],
        timeout: int = 30,
    ) -> Any:
        """等待外部输入并处理

        等待期间向宿主注册“等待该会话（session_id）的下一条消息”，宿主
        收到后经 FeedSessionWait 推送事件并触发本会话；宿主不支持时降级
        为纯本地等待（挂起至超时抛 TimeoutError，或由插件/宿主主动调用
        `await SessionWaiter.trigger(session_id, event)` 喂入消息）。
        """
        self.handler = handler
        USER_SESSIONS[self.session_id] = self

        # 向宿主注册跨进程喂入（失败静默降级，不阻塞本地等待）
        await self._register_host_wait(timeout)

        # 开始一个会话保持事件
        self.session_controller.keep(timeout, reset_timeout=True)

        try:
            return await self.session_controller.future
        except Exception as e:
            self._cleanup(e)
            raise e
        finally:
            self._cleanup()

    def _cleanup(self, error: Exception | None = None) -> None:
        """清理会话"""
        USER_SESSIONS.pop(self.session_id, None)
        try:
            FILTERS.remove(self.session_filter)
        except ValueError:
            pass
        self.session_controller.stop(error)
        # 注销宿主侧等待（幂等：宿主超时已自动注销时不存在的 wait_id 静默；
        # 注册失败 wait_id="" 时跳过）。_cleanup 是同步清理路径，注销经
        # asyncio.to_thread 移出常驻 loop 异步执行，不阻塞清理。
        wait_id, self.wait_id = self.wait_id, ""
        if wait_id:
            try:
                from astrbot.core.star.context import get_host_bridge

                bridge = get_host_bridge()
                if bridge is not None:
                    asyncio.get_event_loop().create_task(
                        bridge.unregister_session_wait_async(wait_id)
                    )
            except Exception as e:
                logger.debug(f"UnregisterSessionWait({wait_id}) 失败: {e}")

    @classmethod
    async def trigger(cls, session_id: str, event: AstrMessageEvent) -> None:
        """外部输入触发会话处理

        宿主经 FeedSessionWait 推送的事件由 `try_trigger` 匹配后调用本方法。
        """
        session = USER_SESSIONS.get(session_id)
        if not session or session.session_controller.future.done():
            return

        async with session._lock:
            if not session.session_controller.future.done():
                if session.record_history_chains:
                    session.session_controller.history_chains.append(
                        [copy.deepcopy(comp) for comp in event.get_messages()],
                    )
                try:
                    # TODO: 这里使用 create_task，跟踪 task，防止超时后这里 handler 仍然在执行
                    assert session.handler is not None
                    await session.handler(session.session_controller, event)
                except Exception as e:
                    session.session_controller.stop(e)


async def try_trigger(event: AstrMessageEvent) -> bool:
    """宿主喂入事件时尝试匹配所有等待中的会话（FeedSessionWait 入口）。

    对齐本体 star_manager 的匹配语义（遍历过滤器做比对）：对 USER_SESSIONS
    中每个 waiter，用其 session_filter.filter(event) 生成 session_id，若与
    waiter.session_id 相等则认为该事件属于此会话，触发后返回 True；
    无匹配返回 False。已结束（future.done）的会话视为不可触发。
    """
    for waiter in list(USER_SESSIONS.values()):
        try:
            session_id = waiter.session_filter.filter(event)
        except Exception as e:
            logger.debug(f"会话过滤器执行失败（忽略）: {e}")
            continue
        if session_id != waiter.session_id:
            continue
        if waiter.session_controller.future.done():
            continue
        await SessionWaiter.trigger(waiter.session_id, event)
        return True
    return False


def session_waiter(timeout: int = 30, record_history_chains: bool = False):
    """装饰器：自动将函数注册为 SessionWaiter 处理函数，并等待外部输入触发执行。

    :param timeout: 超时时间（秒）
    :param record_history_chains: 是否自动记录历史消息链。可以通过 controller.get_history_chains() 获取。深拷贝。

    注（Go 宿主降级）：与 register_wait 相同，等待可能因无消息注入而以
    超时结束；可通过 `SessionWaiter.trigger(session_id, event)` 喂入。
    """

    def decorator(
        func: Callable[[SessionController, AstrMessageEvent], Awaitable[Any]],
    ):
        @functools.wraps(func)
        async def wrapper(
            event: AstrMessageEvent,
            session_filter: SessionFilter | None = None,
            *args,
            **kwargs,
        ):
            if not session_filter:
                session_filter = DefaultSessionFilter()
            if not isinstance(session_filter, SessionFilter):
                raise ValueError("session_filter 必须是 SessionFilter")

            session_id = session_filter.filter(event)
            FILTERS.append(session_filter)

            waiter = SessionWaiter(session_filter, session_id, record_history_chains)
            return await waiter.register_wait(func, timeout)

        return wrapper

    return decorator
