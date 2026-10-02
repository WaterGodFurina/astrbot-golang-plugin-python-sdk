"""共享 Runtime Dispatcher：按 plugin_id 路由 + 插件调用边界的异常捕获。

方案依据（第 7 节「异常隔离边界」）：
- 普通 Python Exception **限制在插件调用边界内**：Dispatcher 捕获、记录
  ``plugin_id``，单插件异常不杀 Runtime；其它插件继续服务。
- 单插件加载失败不导致其它插件失败（除非 Runtime 级致命错误）。
- **无进程级隔离**：``os._exit`` / 解释器级致命错误 / C 扩展崩溃会波及同
  Worker 全部插件——这部分交进程级隔离（python-grpc / python-isolated）。

职责（对齐方案第 3 节「Dispatcher 一律通过 Registry 取当前插件」）：
- 每次分发都经 ``PluginRegistry`` 取 session（禁止全局变量 / 模块扫描）；
- 设/复位当前 PluginSession 的 ContextVar；
- 捕获插件 handler 抛出的普通 Exception，记录到 session（error/health），
  并上报 Watchdog（计数 + 阈值判定，不写死）；
- Runtime 级异常（``BaseException`` 中非 ``Exception`` 的致命项，如
  ``SystemExit`` / ``KeyboardInterrupt``）不吞，交上层处理。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, TYPE_CHECKING

from astrbot._runtime.context import (
    reset_current_session,
    set_current_session,
)
from astrbot._runtime.states import PluginHealthState, PluginLifecycleState

if TYPE_CHECKING:
    from astrbot._runtime.registry import PluginRegistry, PluginSession
    from astrbot._runtime.watchdog import PluginWatchdog

logger = logging.getLogger("astrbot.runtime.dispatcher")


class PluginDispatchError(Exception):
    """插件调用边界内捕获的异常包装（携带 plugin_id 便于宿主日志关联）。"""

    def __init__(self, plugin_id: str, cause: BaseException) -> None:
        super().__init__(f"插件 {plugin_id} 调用失败: {cause}")
        self.plugin_id = plugin_id
        self.cause = cause


class Dispatcher:
    """把请求路由到目标插件的 servicer，并在插件边界内捕获异常。"""

    def __init__(
        self,
        registry: "PluginRegistry",
        servicers: dict,
        watchdog: "PluginWatchdog | None" = None,
    ) -> None:
        self._registry = registry
        self._servicers = servicers
        self._watchdog = watchdog

    def route(self, plugin_id: str) -> tuple["PluginSession", Any]:
        """经 Registry 取 (session, servicer)；找不到抛 KeyError。"""
        session = self._registry.get(plugin_id)
        if session is None:
            raise KeyError(f"plugin_id {plugin_id!r} 不在共享 Runtime 中")
        servicer = self._servicers.get(plugin_id)
        if servicer is None:
            raise KeyError(f"plugin_id {plugin_id!r} 没有对应 servicer")
        return session, servicer

    def dispatch(
        self,
        plugin_id: str,
        method: str,
        *args,
        capture: bool = True,
        **kwargs,
    ) -> Any:
        """设 ContextVar → 委托 servicer 方法 → 复位（异常也复位）。

        capture=True 时把插件边界内的普通 Exception 记入 session 并上报
        Watchdog，然后重新抛出 ``PluginDispatchError``（调用方可据此给宿主
        返回错误，而不是让异常冒泡杀掉 gRPC 工作线程/Runtime）。
        """
        session, servicer = self.route(plugin_id)
        token = set_current_session(session)
        try:
            return getattr(servicer, method)(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- 插件边界捕获（方案第 7 节）
            if not capture:
                raise
            self._record_failure(session, exc)
            raise PluginDispatchError(plugin_id, exc) from exc
        finally:
            reset_current_session(token)

    def _record_failure(self, session: "PluginSession", exc: BaseException) -> None:
        """记录插件级异常：更新 session 错误/健康态并上报 Watchdog。"""
        try:
            session.error = str(exc)
            if session.lifecycle not in (
                PluginLifecycleState.SLEEPING,
                PluginLifecycleState.UNLOADED,
            ):
                session.lifecycle = PluginLifecycleState.ERROR
            if self._watchdog is not None:
                state = self._watchdog.report(
                    session.plugin_id,
                    "handler",
                    lifecycle=session.lifecycle,
                )
                session.health = state
            else:
                session.health = PluginHealthState.DEGRADED
        except Exception:
            # 记录失败不得影响主流程。
            pass
        logger.error("插件 %s 调用异常: %s", session.plugin_id, exc)
