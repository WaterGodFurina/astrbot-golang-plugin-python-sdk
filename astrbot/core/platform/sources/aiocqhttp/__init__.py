"""astrbot.core.platform.sources.aiocqhttp —— OneBot v11 平台消息事件模型
（移植自 Python AstrBot，网络层由宿主 Go aiocqhttp 适配器承担）。"""

from .aiocqhttp_message_event import AiocqhttpMessageEvent

__all__ = ["AiocqhttpMessageEvent"]
