"""插件注册表与会话（共享 Runtime 多插件）。

Dispatcher 一律通过本注册表获取「当前插件」，**禁止**用全局变量 / 模块
扫描结果 / 临时对象判断当前插件（方案文档第十节）。

PluginSession 是单个插件在共享 Runtime 内的**逻辑租户容器**：它持有该插件的
元数据、实例、注册信息（commands/filters/hooks/tools/web_apis）、任务登记
以及健康/生命周期状态。多个 PluginSession 共用一个 Python 解释器。
"""

from __future__ import annotations

import threading
import time
import weakref
from dataclasses import dataclass, field
from typing import Any

from astrbot._runtime.states import (
    PluginHealthState,
    PluginLifecycleState,
)


class TaskRegistry:
    """登记单个插件产生的 asyncio / 后台任务，防止卸载后残留任务持有插件对象。

    仅做登记与批量取消，不在此处实现事件循环调度（复用现有 ``_bridge.loop``）。
    """

    def __init__(self, plugin_id: str = "") -> None:
        self.plugin_id = plugin_id
        self._tasks: "weakref.WeakSet[Any]" = weakref.WeakSet()
        self._lock = threading.Lock()

    def register(self, task: Any) -> None:
        with self._lock:
            self._tasks.add(task)

    def cancel_all(self) -> int:
        """取消登记的全部任务，返回取消数量。失败静默（尽力而为）。"""
        cancelled = 0
        with self._lock:
            tasks = list(self._tasks)
            self._tasks.clear()
        for task in tasks:
            try:
                cancel = getattr(task, "cancel", None)
                if callable(cancel):
                    cancel()
                    cancelled += 1
            except Exception:
                pass
        return cancelled

    def count(self) -> int:
        with self._lock:
            return len(self._tasks)


@dataclass
class PluginSession:
    """共享 Runtime 中单个插件的逻辑租户容器。

    字段对齐方案文档第十节 Plugin Runtime 最少保存项：
    metadata / instance / state / config / handlers / last_activity / error。
    """

    plugin_id: str
    plugin_name: str = ""
    plugin_dir: str = ""
    version: str = ""
    # 插件 Star 实例（实例化后填充）
    instance: Any = None
    # 插件模块（import 后填充）
    module: Any = None
    # 配置（由宿主 GetConfig 拉取）
    config: Any = None

    # 注册信息（Dispatcher 通过 session 获取，而非模块级全局）
    commands: dict = field(default_factory=dict)
    filter_handlers: list = field(default_factory=list)
    hook_handlers: dict = field(default_factory=dict)
    tools: dict = field(default_factory=dict)
    web_apis: list = field(default_factory=list)

    # 生命周期与健康状态
    lifecycle: PluginLifecycleState = PluginLifecycleState.LOADING
    health: PluginHealthState = PluginHealthState.NORMAL
    error: str = ""

    # 活动时间与代际（generation 防止旧实例消息污染）
    last_activity: float = field(default_factory=time.time)
    generation: int = 0

    # 任务登记
    tasks: TaskRegistry = field(default_factory=TaskRegistry)

    def touch(self) -> None:
        """记录一次活动（用于 idle 判定）。"""
        self.last_activity = time.time()

    def reset_failure(self) -> None:
        """恢复为正常：清除故障标记（ISOLATED 不是永久处罚）。"""
        self.health = PluginHealthState.NORMAL
        self.error = ""

    def mark_error(self, message: str) -> None:
        self.error = message
        if self.lifecycle not in (
            PluginLifecycleState.UNLOADED,
            PluginLifecycleState.SLEEPING,
        ):
            self.lifecycle = PluginLifecycleState.ERROR


class PluginRegistry:
    """``plugin_id -> PluginSession`` 的注册表（线程安全）。"""

    def __init__(self) -> None:
        self._sessions: dict[str, PluginSession] = {}
        self._lock = threading.RLock()

    def add(self, session: PluginSession) -> None:
        with self._lock:
            self._sessions[session.plugin_id] = session

    def get(self, plugin_id: str) -> PluginSession | None:
        with self._lock:
            return self._sessions.get(plugin_id)

    def remove(self, plugin_id: str) -> PluginSession | None:
        with self._lock:
            return self._sessions.pop(plugin_id, None)

    def all(self) -> list[PluginSession]:
        with self._lock:
            return list(self._sessions.values())

    def __contains__(self, plugin_id: object) -> bool:
        with self._lock:
            return plugin_id in self._sessions

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)
