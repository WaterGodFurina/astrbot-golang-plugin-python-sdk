"""Runtime / 插件健康状态定义（共享 Runtime 多插件）。

对齐方案文档《Python 运行时多模式共存方案》第六、七节与用户修订后的故障
处理体系：

Runtime 进程状态：
    RUNNING / STOPPED / CRASHED

插件健康状态（生命周期 + 故障判定）：
    NORMAL            正常运行
    DEGRADED          出现一定异常但仍可继续运行（继续观察）
    UNHEALTHY         已达到故障判定条件
    ISOLATION_PENDING 已确认需要脱离 Shared Runtime（由 Go 持久化）
    ISOLATED          已迁移到独立 python-grpc 进程
    RECOVERY_PENDING  更新/恢复后待重新评估（观察窗口内）
    REMOVED           已卸载，从运行状态中删除

注意：本状态是**当前插件实例的运行状态**，不是永久处罚记录。插件恢复、
卸载或更新后都必须有明确的状态清理/重新评估机制（见方案文档）。

同时还有插件生命周期的基础状态（对齐现有语义）：
    LOADING / ACTIVE / IDLE / SLEEPING / UNLOADED / ERROR

其中 ACTIVE→IDLE→SLEEPING→UNLOADED 是**插件休眠**的两级模型；
「插件休眠 ≠ Runtime 休眠」：单个插件 SLEEPING 时 Runtime 仍须为其他插件
服务。
"""

from __future__ import annotations

from enum import Enum


class RuntimeState(str, Enum):
    """Python Runtime **进程**级状态（由 Go Runtime Manager 权威判定）。"""

    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    CRASHED = "CRASHED"


class PluginLifecycleState(str, Enum):
    """插件生命周期状态（Python Runtime 权威）。

    ACTIVE → IDLE → SLEEPING → UNLOADED 为三级收敛；ERROR 为加载/运行失败。
    """

    LOADING = "LOADING"
    ACTIVE = "ACTIVE"
    IDLE = "IDLE"
    SLEEPING = "SLEEPING"
    UNLOADED = "UNLOADED"
    ERROR = "ERROR"


class PluginHealthState(str, Enum):
    """插件健康/故障状态（Python Watchdog 判定，Go 持久化）。

    NORMAL → DEGRADED → UNHEALTHY → ISOLATION_PENDING → ISOLATED，
    以及恢复/卸载/更新后的 RECOVERY_PENDING / REMOVED。
    """

    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    ISOLATION_PENDING = "ISOLATION_PENDING"
    ISOLATED = "ISOLATED"
    RECOVERY_PENDING = "RECOVERY_PENDING"
    REMOVED = "REMOVED"


# 可以被判定为「需要隔离」的终态集合（供 Go 决策参考）。
ISOLATION_TARGET_STATES = frozenset(
    {
        PluginHealthState.ISOLATION_PENDING,
        PluginHealthState.ISOLATED,
    }
)
