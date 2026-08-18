"""AstrBot Core 包（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core` 的对外常量与工具：插件常以
`from astrbot.core import DEMO_MODE, logger` 方式使用。
"""
import logging

# 演示模式（Go 宿主下恒为 False：宿主本身没有演示模式限制）
DEMO_MODE = False

# 顶层日志器：与 astrbot 包共享（插件侧 logger 由 astrbot.api.logger
# 按调用方模块路由，这里仅提供别名，避免插件 import 失败）
logger = logging.getLogger("astrbot")

# 全局异常（对齐本体 astrbot.core.exceptions / pip_installer 导出）
from astrbot.core.exceptions import (  # noqa: E402
    DependencyConflictError,
    ProviderNotFoundError,
)

# html_renderer：占位实例（对齐本体 `from astrbot.core import html_renderer`；
# 独立模块实现，避免与 astrbot.api 产生循环导入）
from astrbot.core.utils.html_renderer import html_renderer  # noqa: E402

# sp：共享偏好存储（对齐本体 `from astrbot.core import sp`）
from astrbot.core.utils.shared_preferences import sp  # noqa: E402
