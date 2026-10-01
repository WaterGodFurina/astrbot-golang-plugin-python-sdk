"""Python 插件共享 Runtime 的内部骨架（多插件共存）。

本包是「单 Python Runtime 承载多插件」改造（见仓库根目录方案文档
`Python运行时多模式共存方案.md`）的 Python 侧基础结构，目前只提供**纯内部**
的注册表 / 会话 / 状态机 / 任务登记骨架，**不改变**现有「一插件一进程」的
`astrbot._bridge.server` 行为。

设计约束（对齐方案文档）：
- 与现有单进程模式**共存**：本包可被未来的 Runtime Host 复用，现有
  `_bridge` 流程不受影响。
- **不宣称进程级隔离**：多个插件共享一个解释器后，普通异常可被调用边界
  捕获，但 `os._exit` / 解释器级致命错误 / C 扩展崩溃会波及同一 Runtime 的
  所有插件。线程/协程/异常捕获均 **不等于** 进程隔离。
- Python Runtime 是 Python 插件生命周期与健康状态的**权威来源**；Go 侧
  只做状态镜像。
"""

from astrbot._runtime.states import (
    PluginHealthState,
    RuntimeState,
)
from astrbot._runtime.registry import (
    PluginRegistry,
    PluginSession,
    TaskRegistry,
)
from astrbot._runtime.watchdog import (
    PluginWatchdog,
    WatchdogConfig,
)

__all__ = [
    "PluginHealthState",
    "RuntimeState",
    "PluginRegistry",
    "PluginSession",
    "TaskRegistry",
    "PluginWatchdog",
    "WatchdogConfig",
]
