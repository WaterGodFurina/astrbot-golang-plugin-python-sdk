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

    def __init__(
        self,
        platform_config: dict | None = None,
        platform_settings: dict | None = None,
        event_queue=None,
        **kwargs,
    ) -> None:
        """兼容本体三位置参数构造（platform_config, platform_settings,
        event_queue），避免插件按本体签名实例化时 TypeError。实际平台
        由 Go 宿主创建，此处仅保存字段供类型面使用。"""
        super().__init__(platform_config or {}, event_queue, **kwargs)
        self.settings = platform_settings or {}
        config = platform_config or {}
        self.host = config.get("ws_reverse_host", "")
        self.port = config.get("ws_reverse_port", 0)