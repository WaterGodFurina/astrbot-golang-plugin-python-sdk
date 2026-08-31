"""MCP 客户端与工具（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.agent.mcp_client`：MCPClient / MCPTool
在此模块路径下可用。Go 宿主插件的 MCP 能力由 SDK 自带（自管连接，不进
宿主 HostService），MCPClient/MCPTool 为纯 re-export，实现在
`astrbot.core.provider.func_tool_manager`（MCP 是真实现）。

宿主 MCP 读写桥接（只读列出 + 调用；插件自管 MCP 不经此通道）：
- `list_host_mcp_tools()`：经 HostBridge.McpListTools 列出宿主已连接
  MCP server 的全部工具（[{server, name, description, schema}]）；
- `HostMcpTool(FunctionTool)`：宿主侧 MCP 工具代理，name/description/
  parameters 从宿主 schema 构造，call 时经 HostBridge.McpCallTool 转发。
  宿主不可用 / 无该 RPC 时优雅降级（列表为空 / 调用返回错误文本，
  对齐 MCP 工具错误语义）。

命名注意：`astrbot.core.agent.mcp_client` 与
`astrbot.core.provider.func_tool_manager` 共用同一份 MCPClient/MCPTool，
避免 SDK 出现两套同名不同义的实现。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.provider.func_tool_manager import (  # noqa: F401
    MCPAllServicesFailedError,
    MCPClient,
    MCPInitError,
    MCPInitSummary,
    MCPInitTimeoutError,
    MCPShutdownTimeoutError,
    MCPTool,
)

logger = logging.getLogger("astrbot")


def _host_bridge():
    """获取宿主桥（薄壳转发入口；不可用返回 None）。"""
    try:
        from astrbot.core.star.context import get_host_bridge

        return get_host_bridge()
    except Exception:
        return None


def _parse_host_tool_schema(raw: Any) -> dict:
    """把宿主工具条目的 schema 字段（dict / schema_json 字符串）规整为
    dict；缺失/非法时回退空 schema（FunctionTool.__post_init__ 会补
    type/properties 默认值）。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            data = json.loads(raw)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def list_host_mcp_tools() -> list[dict]:
    """列出宿主已连接 MCP server 的全部工具（只读）。

    返回 [{"server": str, "name": str, "description": str, "schema": dict},
    ...]（schema 为该工具参数 JSON Schema）。宿主不可用 / bridge 无
    mcp_list_tools / RPC 失败时返回 []（不抛异常）。
    """
    bridge = _host_bridge()
    if bridge is None:
        logger.debug("宿主桥不可用，list_host_mcp_tools 返回空列表")
        return []
    fn = getattr(bridge, "mcp_list_tools", None)
    if fn is None:
        logger.debug("宿主 bridge 未提供 mcp_list_tools（宿主可能不支持）")
        return []
    try:
        raw = fn()
    except Exception as e:
        logger.debug(f"mcp_list_tools 失败（宿主可能不支持）: {e}")
        return []
    out: list[dict] = []
    for item in raw if isinstance(raw, (list, tuple)) else []:
        if not isinstance(item, dict):
            continue
        schema = _parse_host_tool_schema(
            item.get("schema", item.get("schema_json"))
        )
        out.append(
            {
                "server": str(item.get("server", "") or ""),
                "name": str(item.get("name", "") or ""),
                "description": str(item.get("description", "") or ""),
                "schema": schema,
            }
        )
    return out


class HostMcpTool(FunctionTool):
    """宿主侧 MCP 工具代理（FunctionTool 子类，只读+调用）。

    name/description/parameters 从宿主工具 schema 构造；call(context,
    **kwargs) 经 HostBridge.McpCallTool 转发宿主执行，返回纯文本结果。
    is_error 时返回 "error: ..." 前缀文本（对齐本体 MCPTool 的错误语义）。
    """

    def __init__(
        self,
        server: str = "",
        name: str = "",
        description: str = "",
        schema: dict | None = None,
    ) -> None:
        super().__init__(
            name=str(name or ""),
            description=str(description or ""),
            parameters=schema if isinstance(schema, dict) else {},
        )
        self.server: str = str(server or "")
        self.tool_name: str = str(name or "")

    @classmethod
    def from_dict(cls, data: dict) -> "HostMcpTool":
        """从宿主工具条目 dict（{server, name, description, schema|schema_json}）
        构造 HostMcpTool。"""
        if not isinstance(data, dict):
            raise ValueError(f"无法将 {data!r} 解析为宿主 MCP 工具")
        return cls(
            server=str(data.get("server", "") or ""),
            name=str(data.get("name", "") or ""),
            description=str(data.get("description", "") or ""),
            schema=_parse_host_tool_schema(
                data.get("schema", data.get("schema_json"))
            ),
        )

    async def call(self, context: Any, **kwargs: Any) -> ToolExecResult:
        """执行宿主 MCP 工具调用，结果转文本；失败返回错误字符串不抛异常。

        Args:
            context: 运行期上下文包装（ContextWrapper，本体约定首参）。
        """
        bridge = _host_bridge()
        fn = getattr(bridge, "mcp_call_tool", None) if bridge is not None else None
        if fn is None:
            return f"error: MCP 工具 {self.name} 不可用（宿主 MCP 桥未就绪）"
        try:
            result = fn(server=self.server, tool_name=self.tool_name, arguments=kwargs)
        except Exception as exc:  # noqa: BLE001  失败返回错误字符串
            logger.warning("宿主 MCP 工具 %s 调用失败: %s", self.name, exc)
            return f"error: MCP 工具 {self.name} 调用失败: {exc!s}"
        if not isinstance(result, dict):
            return str(result)
        is_error = bool(result.get("is_error", False))
        text = str(result.get("text", "") or "")
        if not text:
            full = result.get("result")
            if full is not None:
                try:
                    text = json.dumps(full, ensure_ascii=False)
                except (TypeError, ValueError):
                    text = str(full)
        if is_error:
            text = f"error: {text}".strip()
        return text or f"error: MCP 工具 {self.name} 返回空结果"


__all__ = [
    "HostMcpTool",
    "MCPAllServicesFailedError",
    "MCPClient",
    "MCPInitError",
    "MCPInitSummary",
    "MCPInitTimeoutError",
    "MCPShutdownTimeoutError",
    "MCPTool",
    "list_host_mcp_tools",
]
