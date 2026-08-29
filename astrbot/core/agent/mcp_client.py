"""MCP 客户端与工具（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.agent.mcp_client`：MCPClient / MCPTool
在此模块路径下可用。Go 宿主插件的 MCP 能力由 SDK 自带（自管连接，不进
宿主 HostService），故本模块为纯 re-export，实现在
`astrbot.core.provider.func_tool_manager`（MCP 是真实现）。

命名注意：`astrbot.core.agent.mcp_client` 与
`astrbot.core.provider.func_tool_manager` 共用同一份 MCPClient/MCPTool，
避免 SDK 出现两套同名不同义的实现。
"""
from __future__ import annotations

from astrbot.core.provider.func_tool_manager import (  # noqa: F401
    MCPAllServicesFailedError,
    MCPClient,
    MCPInitError,
    MCPInitSummary,
    MCPInitTimeoutError,
    MCPShutdownTimeoutError,
    MCPTool,
)

__all__ = [
    "MCPAllServicesFailedError",
    "MCPClient",
    "MCPInitError",
    "MCPInitSummary",
    "MCPInitTimeoutError",
    "MCPShutdownTimeoutError",
    "MCPTool",
]