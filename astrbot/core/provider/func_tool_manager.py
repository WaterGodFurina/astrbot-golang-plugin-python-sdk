"""LLM 工具注册表（Go 宿主兼容运行时，docstring 解析用内置实现替代 docstring_parser）。"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable

from astrbot import logger
from astrbot.core.agent.tool import FunctionTool, ToolSet  # noqa: F401  富 API 工具集合（见 agent/tool.py）
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.core.utils.deprecation import deprecated
from astrbot.core.utils.shared_preferences import sp  # noqa: F401  共享偏好存储（对齐本体 from astrbot.core import sp）

SUPPORTED_TYPES = ["string", "number", "object", "array", "boolean"]

PY_TO_JSON_TYPE = {
    "int": "number",
    "float": "number",
    "bool": "boolean",
    "str": "string",
    "dict": "object",
    "list": "array",
    "tuple": "array",
    "set": "array",
}

# ── MCP 相关常量（对齐原版 func_tool_manager.py）──────────────────────────
DEFAULT_MCP_CONFIG = {"mcpServers": {}}
DEFAULT_MCP_INIT_TIMEOUT_SECONDS = 180.0
DEFAULT_ENABLE_MCP_TIMEOUT_SECONDS = 180.0
MCP_INIT_TIMEOUT_ENV = "ASTRBOT_MCP_INIT_TIMEOUT"
ENABLE_MCP_TIMEOUT_ENV = "ASTRBOT_MCP_ENABLE_TIMEOUT"
MAX_MCP_TIMEOUT_SECONDS = 300.0


class MCPInitError(Exception):
    """MCP 初始化失败的基础异常。"""


class MCPInitTimeoutError(asyncio.TimeoutError, MCPInitError):
    """MCP 客户端初始化超过配置超时。"""


class MCPAllServicesFailedError(MCPInitError):
    """所有配置的 MCP 服务均初始化失败。"""


class MCPShutdownTimeoutError(asyncio.TimeoutError):
    """MCP 关闭超过配置超时。"""

    def __init__(self, names: list[str], timeout: float) -> None:
        self.names = names
        self.timeout = timeout
        message = f"MCP 服务关闭超时（{timeout:g} 秒）：{', '.join(names)}"
        super().__init__(message)


@dataclass
class MCPInitSummary:
    """MCP 初始化结果汇总。"""

    total: int = 0
    success: int = 0
    failed: list[str] = field(default_factory=list)


@dataclass
class _MCPServerRuntime:
    """MCP 服务运行期元数据（占位：SDK 无 MCP 依赖，client 用 Any 兜底）。"""

    name: str = ""
    client: Any = None
    shutdown_event: Any = None
    lifecycle_task: Any = None


class _MCPClientDictView(Mapping[str, Any]):
    """MCP 客户端的只读映射视图（由运行期状态推导）。"""

    def __init__(self, runtime: dict | None = None) -> None:
        self._runtime: dict = runtime if runtime is not None else {}

    def __getitem__(self, key: str) -> Any:
        return self._runtime[key].client

    def __iter__(self):
        return iter(self._runtime)

    def __len__(self) -> int:
        return len(self._runtime)


def _resolve_timeout(
    timeout: float | int | str | None = None,
    *,
    env_name: str = MCP_INIT_TIMEOUT_ENV,
    default: float = DEFAULT_MCP_INIT_TIMEOUT_SECONDS,
) -> float:
    """解析超时：优先级为 显式参数 > 环境变量 > 默认值。"""
    source = f"环境变量 {env_name}"
    if timeout is None:
        timeout = os.getenv(env_name, str(default))
    else:
        source = "显式参数 timeout"

    try:
        timeout_value = float(timeout)
    except (TypeError, ValueError):
        logger.warning(
            f"超时配置（{source}）={timeout!r} 无效，使用默认值 {default:g} 秒。"
        )
        return default

    if timeout_value <= 0:
        logger.warning(
            f"超时配置（{source}）={timeout_value:g} 必须大于 0，使用默认值 {default:g} 秒。"
        )
        return default

    if timeout_value > MAX_MCP_TIMEOUT_SECONDS:
        logger.warning(
            f"超时配置（{source}）={timeout_value:g} 过大，已限制为最大值 "
            f"{MAX_MCP_TIMEOUT_SECONDS:g} 秒，以避免长时间等待。"
        )
        return MAX_MCP_TIMEOUT_SECONDS

    return timeout_value


def _prepare_config(config: dict) -> dict:
    """准备 MCP 配置，处理嵌套格式（mcpServers 包裹 / active 字段）。"""
    config = dict(config or {})
    if config.get("mcpServers"):
        first_key = next(iter(config["mcpServers"]))
        config = config["mcpServers"][first_key]
    config.pop("active", None)
    return config


async def _quick_test_mcp_connection(config: dict) -> tuple[bool, str]:
    """快速测试 MCP 服务器可达性（降级：SDK 无 aiohttp 依赖，恒返回 False）。"""
    try:
        _prepare_config(config or {})
    except Exception as exc:  # noqa: BLE001  仅记录后返回
        return False, f"{exc!s}"
    logger.warning("SDK 无 MCP 依赖，跳过 MCP 服务器连接测试。")
    return False, "SDK 不支持 MCP 连接测试（无 aiohttp 依赖）"


class MCPClient:
    """MCP 客户端（占位降级：SDK 无 MCP 依赖）。

    connect_to_server 返回 False 并记录日志，其余方法为空实现，保证
    插件代码 import / 调用不崩。
    """

    def __init__(self) -> None:
        self.name: str = ""
        self.tools: list = []

    async def connect_to_server(self, config: dict, name: str = "") -> bool:
        """连接 MCP 服务器（降级：恒返回 False）。"""
        self.name = name or self.name
        logger.warning("MCP 客户端未实现（SDK 无 MCP 依赖），连接 %s 失败", self.name)
        return False

    async def list_tools_and_save(self):
        """列出并保存工具（降级：返回 None）。"""
        return None

    async def cleanup(self) -> None:
        """清理资源（降级：空实现）。"""
        return None


class MCPTool(FunctionTool):
    """MCP 工具（占位降级类）。

    原版来自 astrbot.core.agent.mcp_client.MCPTool（基于 mcp SDK 的
    ClientTool），SDK 无 MCP 依赖，这里退化为普通 FunctionTool 子类，
    仅搬运 name/description/parameters 与 MCP 来源标记。
    """

    def __init__(
        self,
        mcp_tool: Any = None,
        mcp_client: Any = None,
        mcp_server_name: str = "",
    ) -> None:
        name = getattr(mcp_tool, "name", "") or ""
        description = getattr(mcp_tool, "description", "") or ""
        parameters = getattr(mcp_tool, "parameters", None) or {}
        if not isinstance(parameters, dict):
            parameters = {}
        super().__init__(name=name, description=description, parameters=parameters)
        self.mcp_server_name: str = mcp_server_name or ""
        self.mcp_client: Any = mcp_client

    async def call(self, context: Any, **kwargs: Any) -> Any:
        """执行 MCP 工具调用（降级：无底层实现，返回错误提示）。"""
        return f"error: MCP 工具 {self.name} 不可用（SDK 无 MCP 依赖）"


class _PermissionGuardedTool(FunctionTool):
    """工具权限守卫（占位降级：直接透传被包装工具的执行逻辑）。

    原版在调用前做 per-tool 权限检查（_check_tool_permission），SDK 简化
    为透传，不检查权限。
    """

    def __init__(self, tool: Any, manager: Any) -> None:
        super().__init__(
            name=getattr(tool, "name", "") or "",
            description=getattr(tool, "description", "") or "",
            parameters=getattr(tool, "parameters", None) or {},
        )
        self._wrapped = tool
        self._mgr = manager
        self.active = getattr(tool, "active", True)

    async def call(self, context: Any, **kwargs: Any) -> Any:
        """透传执行被包装工具（优先 handler，其次覆写的 call）。"""
        call_override = getattr(type(self._wrapped), "call", None)
        if call_override is not None and call_override is not FunctionTool.call:
            return await call_override(self._wrapped, context, **kwargs)

        handler = getattr(self._wrapped, "handler", None)
        if handler is not None:
            result = handler(context, **kwargs)
            if asyncio.iscoroutine(result):
                return await result
            if hasattr(result, "__aiter__"):
                last: Any = None
                async for item in result:
                    last = item
                return last
            return result

        return "error: tool has no callable handler"


async def ensure_builtin_tools_loaded() -> None:
    """加载 AstrBot 内置工具（占位：SDK 无内置工具，空实现）。

    原版由 star_manager / 启动流程调用，SDK 没有对应调用点，只定义函数
    供插件 await 调用不报错。
    """
    return None


def get_builtin_tool_class(name: str) -> type | None:
    """按名称取内置工具类（SDK 无内置工具，恒 None）。"""
    return None


def get_builtin_tool_name(tool_cls: type) -> str | None:
    """按类取内置工具名（SDK 无内置工具，恒 None）。"""
    return None


def iter_builtin_tool_classes():
    """迭代所有内置工具类（SDK 无内置工具，返回空列表）。"""
    return []


class DocParam:
    def __init__(self, arg_name: str, type_name: str | None, description: str):
        self.arg_name = arg_name
        self.type_name = type_name
        self.description = description


class DocString:
    def __init__(self):
        self.description: str | None = None
        self.params: list[DocParam] = []


def parse_docstring(doc: str) -> DocString:
    """简化版 docstring 解析：支持 Google 风格 Args: 与 :param 风格。"""
    result = DocString()
    if not doc:
        return result
    lines = doc.splitlines()
    param_pattern = re.compile(r"^\s*(?:Args|参数)[:\s]*(.*)$", re.I)
    param_line = re.compile(r"^\s*(\w+)\s*(?:\(([^)]*)\))?\s*:\s*(.*)$")
    colon_param = re.compile(r"^\s*:param\s+(\w+)(?:\s*:\s*(\w+))?\s*:\s*(.*)$")

    description_lines: list[str] = []
    in_args = False
    current_type_ctx = ""
    # Google 风格 docstring 的段标记：命中即结束 Args 参数解析，避免
    # "Returns:" 这类行被误解析为名为 Returns 的无类型参数（随后
    # register_llm_tool 因缺类型注释抛 ValueError → 插件加载失败）。
    section_markers = re.compile(r"^\s*(?:Returns|Raises|Yields|Example|Examples|Note|Warning|Raises:)\s*:", re.I)
    for raw in lines:
        line = raw.strip()
        if not line:
            if not in_args and description_lines and description_lines[-1]:
                description_lines.append("")
            continue
        if in_args and section_markers.match(raw):
            in_args = False
            current_type_ctx = ""
            description_lines.append(line)
            continue
        m = colon_param.match(raw)
        if m:
            in_args = True
            result.params.append(DocParam(m.group(1), m.group(2), m.group(3).strip()))
            continue
        m = param_pattern.match(raw)
        if m:
            in_args = True
            if m.group(1).strip().lower().startswith("type"):
                continue
            continue
        if in_args:
            m = param_line.match(raw)
            if m and (m.group(2) or ":" in raw):
                result.params.append(DocParam(m.group(1), m.group(2), m.group(3).strip()))
                current_type_ctx = m.group(2) or ""
                continue
            if current_type_ctx and result.params:
                result.params[-1].description += " " + line
            continue
        description_lines.append(line)

    result.description = "\n".join(description_lines).strip()
    return result


class FuncTool:
    def __init__(self, name: str, parameters: dict, description: str, handler: Callable):
        self.name = name
        self.parameters = parameters
        self.description = description
        self.handler = handler
        self.active = True  # activate_llm_tool / deactivate_llm_tool 控制

    def to_schema(self) -> dict:
        schema = {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description or "",
                "parameters": self.parameters or {"type": "object", "properties": {}},
            },
        }
        return schema


class FunctionToolManager:
    def __init__(self) -> None:
        self.func_list: list[FuncTool] = []

    def spec_to_func(self, name: str, func_args: list[dict], desc: str, handler: Callable) -> FuncTool:
        params = {"type": "object", "properties": {}}
        for param in func_args:
            p = copy.deepcopy(param)
            p.pop("name", None)
            params["properties"][param["name"]] = p
        return FuncTool(name=name, parameters=params, description=desc, handler=handler)

    def add_func(self, name: str, func_args: list, desc: str, handler: Callable) -> None:
        self.remove_func(name)
        self.func_list.append(self.spec_to_func(name=name, func_args=func_args, desc=desc, handler=handler))

    def remove_func(self, name: str) -> None:
        self.func_list = [f for f in self.func_list if f.name != name]

    def get_func_by_name(self, name: str) -> FuncTool | None:
        for f in self.func_list:
            if f.name == name:
                return f
        return None

    def activate(self, name: str) -> bool:
        for f in self.func_list:
            if f.name == name:
                f.active = True
                return True
        return False

    def deactivate(self, name: str) -> bool:
        for f in self.func_list:
            if f.name == name:
                f.active = False
                return True
        return False

    def list_funcs(self, only_active: bool = False) -> list[FuncTool]:
        if not only_active:
            return list(self.func_list)
        return [f for f in self.func_list if f.active]


llm_tools = FunctionToolManager()
"""全局 LLM 函数工具注册表"""

# 别名（对齐原版 func_tool_manager.py 末尾的 FuncCall = FunctionToolManager）。
FuncCall = FunctionToolManager
