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
