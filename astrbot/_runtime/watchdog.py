"""Python 插件健康 Watchdog（共享 Runtime 多插件）。

职责边界（方案文档）：**Python Watchdog 管插件，Golang Runtime Manager 管
进程**。本模块只负责在共享 Runtime 内收集并判定**插件级**健康状态：

- 普通业务异常（handler exception / API timeout / background task failure）
  采用「计数 + 时间窗口 + 阈值」判定，不因一次 Exception 就隔离；
- 明确的致命生命周期错误（初始化/注册/依赖加载失败、session 失效、
  dispatcher 无法继续工作）可直接进入 UNHEALTHY；
- 达到 UNHEALTHY 后通过回调上报 Go（Go 负责持久化 ISOLATION_PENDING 与
  隔离迁移）；
- SLEEPING / UNLOADED 插件不参与异常判定；「长时间无活动」不等于崩溃；
- **Runtime 整体崩溃**（SIGSEGV / OOM / os._exit）不在本模块职责内——此时
  Watchdog 自身也已消失，必须由 Go Runtime Manager 负责检测与重启。

阈值全部可配置，不在架构层写死（方案文档）。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from astrbot._runtime.states import PluginHealthState, PluginLifecycleState


@dataclass
class WatchdogConfig:
    """健康判定阈值（可配置）。"""

    # 统计时间窗口（秒）
    window_seconds: float = 60.0
    # 进入 DEGRADED 的异常计数阈值
    degraded_threshold: int = 2
    # 进入 UNHEALTHY 的异常计数阈值
    unhealthy_threshold: int = 8
    # 进入 UNHEALTHY 的超时计数阈值
    unhealthy_timeout_threshold: int = 5


@dataclass
class _FailureWindow:
    """单个插件的滚动失败计数。"""

    events: deque = field(default_factory=deque)  # (timestamp, category)
    declared_unhealthy: bool = False

    def prune(self, now: float, window: float) -> None:
        cutoff = now - window
        while self.events and self.events[0][0] < cutoff:
            self.events.popleft()

    def count(self, category: str) -> int:
        return sum(1 for _, c in self.events if c == category)


# 失败类别
CATEGORY_HANDLER = "handler"
CATEGORY_TIMEOUT = "timeout"
CATEGORY_BACKGROUND = "background"


class PluginWatchdog:
    """判定共享 Runtime 内插件的健康状态。

    判定是**插件级**的；不监控单个插件进程（共享进程下没有独立进程可监控）。
    """

    def __init__(
        self,
        config: WatchdogConfig | None = None,
        on_unhealthy: Callable[[str, str], None] | None = None,
    ) -> None:
        self.config = config or WatchdogConfig()
        self._on_unhealthy = on_unhealthy
        self._windows: dict[str, _FailureWindow] = {}
        self._lock = threading.Lock()
        self._heartbeat_at: float = 0.0

    # ---- 事件上报 ----
    def report(
        self,
        plugin_id: str,
        category: str,
        lifecycle: PluginLifecycleState | None = None,
    ) -> PluginHealthState:
        """登记一次插件级失败事件并返回判定后的健康状态。

        lifecycle 为 SLEEPING / UNLOADED 时不计入（避免休眠插件被误判）。
        """
        if lifecycle in (
            PluginLifecycleState.SLEEPING,
            PluginLifecycleState.UNLOADED,
        ):
            return PluginHealthState.NORMAL

        now = time.time()
        with self._lock:
            win = self._windows.setdefault(plugin_id, _FailureWindow())
            win.events.append((now, category))
            win.prune(now, self.config.window_seconds)

            handler_n = win.count(CATEGORY_HANDLER)
            timeout_n = win.count(CATEGORY_TIMEOUT)
            bg_n = win.count(CATEGORY_BACKGROUND)

            if (
                handler_n >= self.config.unhealthy_threshold
                or timeout_n >= self.config.unhealthy_timeout_threshold
                or bg_n >= self.config.unhealthy_threshold
            ):
                state = PluginHealthState.UNHEALTHY
            elif (
                handler_n + timeout_n + bg_n >= self.config.degraded_threshold
            ):
                state = PluginHealthState.DEGRADED
            else:
                state = PluginHealthState.NORMAL

            should_report = (
                state == PluginHealthState.UNHEALTHY and not win.declared_unhealthy
            )
            if should_report:
                win.declared_unhealthy = True

        # 回调放锁外，避免在持锁时触发 Go 上报 / 阻塞。
        if should_report and self._on_unhealthy is not None:
            try:
                self._on_unhealthy(plugin_id, "plugin_unhealthy")
            except Exception:
                pass
        return state

    def mark_fatal(self, plugin_id: str, reason: str) -> PluginHealthState:
        """致命生命周期错误：直接 UNHEALTHY，无需计数。"""
        with self._lock:
            win = self._windows.setdefault(plugin_id, _FailureWindow())
            already = win.declared_unhealthy
            win.declared_unhealthy = True
        if not already and self._on_unhealthy is not None:
            try:
                self._on_unhealthy(plugin_id, reason)
            except Exception:
                pass
        return PluginHealthState.UNHEALTHY

    def clear(self, plugin_id: str) -> None:
        """清除某插件的失败窗口（恢复 / 卸载 / 更新后调用）。"""
        with self._lock:
            self._windows.pop(plugin_id, None)

    # ---- Runtime 心跳（进程级，方案第 6 节）----
    # Python Watchdog **不**负责检测 Runtime 整体崩溃（SIGSEGV/OOM/os._exit
    # 时它自身也已消失）；进程级监控由 Go Runtime Manager 负责。此处只提供
    # 一个**心跳时间戳**，由 Runtime 主循环低频刷新并经 HealthCheck 上报，供
    # Go 侧判断 Runtime 是否卡死（连接存活但心跳停滞 = 疑似 hang）。
    def heartbeat(self) -> None:
        """刷新 Runtime 心跳时间戳（Runtime 主循环周期调用）。"""
        self._heartbeat_at = time.time()

    def last_heartbeat(self) -> float:
        """返回最近一次心跳时间（Unix 秒；0 = 从未心跳）。"""
        return getattr(self, "_heartbeat_at", 0.0)

    def snapshot(self, plugin_id: str) -> dict:
        with self._lock:
            win = self._windows.get(plugin_id)
            if win is None:
                return {"handler": 0, "timeout": 0, "background": 0}
            return {
                "handler": win.count(CATEGORY_HANDLER),
                "timeout": win.count(CATEGORY_TIMEOUT),
                "background": win.count(CATEGORY_BACKGROUND),
            }
