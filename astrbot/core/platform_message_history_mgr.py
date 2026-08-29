"""平台消息历史管理器（Go 宿主兼容运行时，对齐本体 platform_message_history_mgr）。

SDK 已提供可用的真实现（JSON 文件持久化）于
`astrbot.core.utils.message_history_manager`，本模块 re-export 同一对象，
保证 `from astrbot.core.platform_message_history_mgr import
PlatformMessageHistoryManager` 路径与本体一致。

命名注意：与 `utils.message_history_manager` 共用同一实现，避免 SDK 出现
两套同名不同义的管理器。
"""
from __future__ import annotations

from astrbot.core.utils.message_history_manager import (  # noqa: F401
    PlatformMessageHistory,
    PlatformMessageHistoryManager,
)

__all__ = ["PlatformMessageHistory", "PlatformMessageHistoryManager"]