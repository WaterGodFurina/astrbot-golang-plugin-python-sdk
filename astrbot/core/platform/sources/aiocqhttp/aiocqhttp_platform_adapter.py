"""aiocqhttp 平台适配器 stub（Go 宿主兼容运行时）。

Go 宿主下 aiocqhttp 适配器由 Go 侧实现，本模块仅提供插件 import
所需的类名（isinstance / 类型标注），不做实际平台注册。
"""
from astrbot.core.platform import Platform
from astrbot.core.platform.register import register_platform_adapter


@register_platform_adapter(
    "aiocqhttp",
    "适用于 OneBot V11 标准的消息平台适配器，支持反向 WebSockets。",
    support_streaming_message=False,
)
class AiocqhttpAdapter(Platform):
    """占位实现：Go 宿主下 aiocqhttp 适配器实际由 Go 侧提供。"""
    pass