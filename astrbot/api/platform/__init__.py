"""astrbot.api.platform —— 平台相关模型与适配器注册（re-export 自 core）。

对齐 Python 本体 `astrbot.api.platform` 的 __all__：Group / MessageMember /
AstrBotMessage / AstrMessageEvent / MessageType / Platform /
PlatformMetadata / register_platform_adapter。
"""

# 消息组件（原版在文件顶部 `from astrbot.core.message.components import *`，
# 使 `from astrbot.api.platform import Plain, Image` 可用）
from astrbot.core.message.components import *  # noqa: F401,F403

from astrbot.core.platform import (  # noqa: F401
    AstrBotMessage,
    AstrMessageEvent,
    Group,
    MessageMember,
    MessageType,
    Platform,
    PlatformMetadata,
)
from astrbot.core.platform.register import (  # noqa: F401
    register_platform_adapter,
)

__all__ = [
    "AstrBotMessage",
    "AstrMessageEvent",
    "Group",
    "MessageMember",
    "MessageType",
    "Platform",
    "PlatformMetadata",
    "register_platform_adapter",
]
