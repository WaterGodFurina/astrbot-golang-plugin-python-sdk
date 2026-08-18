"""指令配置同步（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.star.command_management`：提供
`sync_command_configs`。SDK 宿主侧管理指令配置（Go 侧），这里降级为
no-op，仅保证 import 与调用不报错。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("astrbot")


async def sync_command_configs() -> None:
    """同步指令配置，清理过期配置（SDK 降级：no-op）。"""
