"""日志管理器（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.log.LogManager`：插件主要用到
`LogManager.get_plugin_logger(name)` 获取插件专属 logger。
"""
import logging


class LogManager:
    """插件日志管理器（简化实现）。"""

    @staticmethod
    def get_plugin_logger(plugin_name: str) -> logging.Logger:
        """返回插件专属 logger（astrbot.plugin.<name>）。"""
        return logging.getLogger(f"astrbot.plugin.{plugin_name}")

    @staticmethod
    def get_default_logger() -> logging.Logger:
        return logging.getLogger("astrbot")
