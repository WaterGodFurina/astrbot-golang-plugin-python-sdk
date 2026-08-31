"""agent 子包（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.agent`：提供 LLM 函数工具（FunctionTool）
等与插件交互的核心抽象。具体实现见 `astrbot.core.agent.tool`；宿主
MCP 工具桥接（list_host_mcp_tools / HostMcpTool）见
`astrbot.core.agent.mcp_client`。
"""
from __future__ import annotations

__all__ = ["HostMcpTool", "list_host_mcp_tools"]


def __getattr__(name: str):
    # 懒导出：避免包 __init__ 在 import 期拉起 func_tool_manager / mcp
    # 等重依赖（保持 `import astrbot.core.agent` 轻量且无循环导入风险）。
    if name in __all__:
        from astrbot.core.agent import mcp_client

        return getattr(mcp_client, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
