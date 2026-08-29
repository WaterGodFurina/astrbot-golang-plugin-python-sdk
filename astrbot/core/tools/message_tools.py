"""消息工具（Go 宿主兼容运行时，对齐本体 tools/message_tools.py）。

SDK 薄壳：`SendMessageToUserTool` / `GetGroupMessageHistoryTool` 定义对齐本体，
宿主消息系统（SendMessage / 消息历史 RPC）原生执行。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.core.agent.tool import FunctionTool


@dataclass
class SendMessageToUserTool(FunctionTool):
    """向指定用户/会话主动发消息（宿主 SendMessage 原生执行）。"""

    name: str = "send_message_to_user"
    description: str = "Send a message to a specific user or session proactively."
    parameters: dict = field(default_factory=dict)


@dataclass
class GetGroupMessageHistoryTool(FunctionTool):
    """读取群聊消息历史（宿主消息历史存储原生执行）。"""

    name: str = "get_group_message_history"
    description: str = "Get the recent message history of a group chat."
    parameters: dict = field(default_factory=dict)


__all__ = ["GetGroupMessageHistoryTool", "SendMessageToUserTool"]