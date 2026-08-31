"""日志管理器（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.log.LogManager`：插件主要用到
`LogManager.get_plugin_logger(name)` 获取插件专属 logger，以及高频用法
`LogManager.GetLogger(log_name)`（本体为类方法，返回标准 logging.Logger）。

Go 宿主运行时无 loguru / 文件 sink / WebUI 日志桥（LogBroker 订阅分发）
等基础设施，真正的日志 handler 由 `astrbot._bridge.server._setup_logging()`
统一配置到 root logger；因此 GetLogger 仅保证返回可用的标准 Logger，
不做 loguru 拦截与级别覆写，避免断开 root 传播导致日志黑洞。
"""
import asyncio
import logging
from collections import deque

PLUGIN_LOGGER_PREFIX = "astrbot.plugin."
"""插件专属 logger 名前缀；完整名为 ``astrbot.plugin.<plugin_name>``（对齐本体）。"""

PLUGIN_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
"""允许的插件日志级别名（对齐本体）。"""

CACHED_SIZE = 500
"""日志缓存条数上限（对齐本体常量）。"""


class LogBroker:
    """日志代理类：缓存与分发日志条目（对齐本体 core.log.LogBroker）。

    Go 宿主运行时无 WebUI 日志订阅管线，这里保留同名同接口的最小实现：
    publish 写入有界缓存并投递给已注册订阅者（asyncio.Queue），插件或
    内部代码引用它时行为一致、不炸。
    """

    def __init__(self) -> None:
        self.log_cache: deque = deque(maxlen=CACHED_SIZE)
        self.subscribers: list[asyncio.Queue] = []

    def register(self) -> asyncio.Queue:
        """注册一个订阅队列，返回给调用方消费。"""
        q: asyncio.Queue = asyncio.Queue(maxsize=CACHED_SIZE + 10)
        self.subscribers.append(q)
        return q

    def unregister(self, q: asyncio.Queue) -> None:
        """注销一个订阅队列。"""
        if q in self.subscribers:
            self.subscribers.remove(q)

    def publish(self, log_entry: dict) -> None:
        """发布一条日志条目：写入缓存并分发到所有订阅者。"""
        self.log_cache.append(log_entry)
        for q in self.subscribers:
            try:
                q.put_nowait(log_entry)
            except asyncio.QueueFull:
                pass


class LogManager:
    """插件日志管理器（简化实现，接口对齐本体）。"""

    @classmethod
    def GetLogger(cls, log_name: str = "default") -> logging.Logger:
        """获取/创建指定名称的 logger（对齐本体类方法签名）。

        本体版本会接入 loguru 拦截 handler 并强制 DEBUG + propagate=False；
        Go 宿主运行时的日志 handler 由 `astrbot._bridge.server._setup_logging()`
        统一挂在 root logger 上，这里若照搬会断开传播造成日志丢失，因此
        仅返回标准 `logging.getLogger(log_name)`（沿用宿主日志管线）。

        Args:
            log_name: logger 名称，如 ``"astrbot"`` 或插件自定义名。

        Returns:
            标准库 logging.Logger。
        """
        return logging.getLogger(log_name)

    @staticmethod
    def get_plugin_logger(plugin_name: str) -> logging.Logger:
        """返回插件专属 logger（astrbot.plugin.<name>）。"""
        return logging.getLogger(f"{PLUGIN_LOGGER_PREFIX}{plugin_name}")

    @staticmethod
    def get_default_logger() -> logging.Logger:
        return logging.getLogger("astrbot")
