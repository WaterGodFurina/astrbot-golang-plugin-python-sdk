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
from types import MappingProxyType
from typing import Any, Callable

from astrbot import logger
from astrbot.core.agent.tool import FunctionTool, ToolSet  # noqa: F401  富 API 工具集合（见 agent/tool.py）
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.core.utils.deprecation import deprecated
from astrbot.core.utils.shared_preferences import sp  # noqa: F401  共享偏好存储（对齐本体 from astrbot.core import sp）

# ── MCP 真实实现（对齐原版 astrbot.core.agent.mcp_client）───────────────────
# 顶层保护性 import：mcp/anyio 随宿主依赖一起安装（见主仓库 embed.go 的
# hostBaseDeps），此处缺失时 MCPClient/MCPTool 退化为占位行为，不崩、不影响
# 其它工具。仅当插件主动使用 MCPClient 时才会真正工作，普通调用路径零开销。
try:
    import anyio
    import mcp
    from contextlib import AsyncExitStack
    from mcp.client.sse import sse_client
    from mcp.client.streamable_http import streamable_http_client

    _MCP_AVAILABLE = True
except (ModuleNotFoundError, ImportError):
    _MCP_AVAILABLE = False
    logger.warning("Warning: Missing 'mcp' dependency, MCP services will be unavailable.")

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
    """快速测试 MCP 服务器可达性（真实 HTTP 探测；无 mcp/aiohttp 时返回 False）。"""
    if not _MCP_AVAILABLE:
        logger.warning("SDK 无 MCP 依赖，跳过 MCP 服务器连接测试。")
        return False, "SDK 不支持 MCP 连接测试（无 mcp 依赖）"
    import aiohttp

    cfg = _prepare_config(config or {})
    url = cfg.get("url")
    if not url:
        return False, "MCP connection config missing url"
    headers = cfg.get("headers", {})
    timeout = cfg.get("timeout", 10)
    transport = cfg.get("transport") or cfg.get("type") or ""
    try:
        async with aiohttp.ClientSession() as session:
            if transport == "streamable_http":
                payload = {
                    "jsonrpc": "2.0",
                    "method": "initialize",
                    "id": 0,
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test-client", "version": "1.2.3"},
                    },
                }
                async with session.post(
                    url,
                    headers={
                        **headers,
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream",
                    },
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as response:
                    if response.status == 200:
                        return True, ""
                    return False, f"HTTP {response.status}: {response.reason}"
            else:
                async with session.get(
                    url,
                    headers={**headers, "Accept": "application/json, text/event-stream"},
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as response:
                    if response.status == 200:
                        return True, ""
                    return False, f"HTTP {response.status}: {response.reason}"
    except asyncio.TimeoutError:
        return False, f"Connection timeout: {timeout} seconds"
    except Exception as exc:  # noqa: BLE001  仅记录后返回
        return False, f"{exc!s}"



class MCPClient:
    """MCP 客户端（真实实现，对齐原版 astrbot.core.agent.mcp_client.MCPClient）。

    每个连接跑在专用 asyncio.Task（_connection_task）里，由该任务持有
    AsyncExitStack 与所有 anyio cancel scope，避免
    `RuntimeError: Attempted to exit cancel scope in a different task`。
    仅当插件主动使用 MCPClient 时才会建立连接，普通调用路径零开销。
    """

    def __init__(self) -> None:
        self.name: str = ""
        self.tools: list = []
        self.session: Any = None
        self.active: bool = True
        self.exit_stack: Any = None
        self._connection_task: asyncio.Task | None = None
        self._old_connection_tasks: list[asyncio.Task] = []
        self._mcp_server_config: dict | None = None
        self._server_name: str = ""

    async def _run_connection(
        self, mcp_server_config: dict, name: str, ready: asyncio.Future
    ) -> None:
        """持有一次连接的完整生命周期（总是在专用 Task 中运行）。"""
        stack = self.exit_stack = AsyncExitStack()
        try:
            try:
                await self._do_connect(mcp_server_config, name)
            except Exception as exc:
                if not ready.done():
                    ready.set_exception(exc)
                raise
            else:
                if not ready.done():
                    ready.set_result(None)
            await asyncio.Event().wait()
        finally:
            try:
                await stack.aclose()
            except Exception as exc:  # noqa: BLE001  仅记录
                logger.debug(f"Error closing exit stack for {name}: {exc}")
            if self.exit_stack is stack:
                self.exit_stack = None
            if not ready.done():
                ready.set_exception(RuntimeError("Connection task exited early"))

    async def connect_to_server(self, config: dict, name: str = "") -> bool:
        """连接 MCP 服务器（成功返回 True，失败记录日志返回 False）。"""
        self.name = name or self.name
        self._mcp_server_config = config
        self._server_name = self.name
        if not _MCP_AVAILABLE:
            logger.warning("MCP 客户端不可用（SDK 无 mcp 依赖），连接 %s 失败", self.name)
            return False
        ready: asyncio.Future = asyncio.get_running_loop().create_future()
        if self._connection_task and not self._connection_task.done():
            self._cancel_connection_task(self._connection_task)
            self._connection_task = None
        self._connection_task = asyncio.create_task(
            self._run_connection(config, self.name, ready),
            name=f"mcp-conn:{self.name}",
        )
        try:
            await ready
        except asyncio.CancelledError:
            if self._connection_task and not self._connection_task.done():
                self._cancel_connection_task(self._connection_task)
            self._connection_task = None
            raise
        except Exception as exc:
            logger.warning("MCP 连接 %s 失败: %s", self.name, exc)
            if self._connection_task and not self._connection_task.done():
                self._old_connection_tasks.append(self._connection_task)
            self._connection_task = None
            return False
        try:
            await self.list_tools_and_save()
        except Exception as exc:  # noqa: BLE001
            logger.warning("MCP 列出工具 %s 失败: %s", self.name, exc)
            await self.cleanup()
            return False
        return True

    async def _do_connect(self, mcp_server_config: dict, name: str) -> None:
        """内部：在 _run_connection 的任务内执行真实连接。"""
        assert self.exit_stack is not None
        cfg = _prepare_config(mcp_server_config or {})

        def logging_callback(msg: Any) -> None:
            return None

        if "url" in cfg:
            success, error_msg = await _quick_test_mcp_connection(cfg)
            if not success:
                raise Exception(error_msg)
            transport_type = cfg.get("transport") or cfg.get("type")
            if transport_type == "streamable_http":
                http_client = await self.exit_stack.enter_async_context(
                    mcp.httpx.AsyncClient(
                        headers=cfg.get("headers", {}),
                        timeout=mcp.httpx.Timeout(
                            cfg.get("timeout", 30),
                            read=cfg.get("sse_read_timeout", 60 * 5),
                        ),
                        follow_redirects=True,
                    )
                )
                streams_ctx = streamable_http_client(
                    url=cfg["url"],
                    http_client=http_client,
                    terminate_on_close=cfg.get("terminate_on_close", True),
                )
                read_s, write_s, _ = await self.exit_stack.enter_async_context(streams_ctx)
                self.session = await self.exit_stack.enter_async_context(
                    mcp.ClientSession(
                        read_stream=read_s,
                        write_stream=write_s,
                        read_timeout_seconds=cfg.get("session_read_timeout", 60),
                    )
                )
            else:
                streams_ctx = sse_client(
                    url=cfg["url"],
                    headers=cfg.get("headers", {}),
                    timeout=cfg.get("timeout", 5),
                    sse_read_timeout=cfg.get("sse_read_timeout", 60 * 5),
                )
                streams = await self.exit_stack.enter_async_context(streams_ctx)
                self.session = await self.exit_stack.enter_async_context(
                    mcp.ClientSession(
                        *streams,
                        read_timeout_seconds=cfg.get("session_read_timeout", 60),
                    )
                )
        else:
            server_params = mcp.StdioServerParameters(
                command=cfg.get("command"),
                args=cfg.get("args", []),
                env=cfg.get("env"),
            )
            stdio_transport = await self.exit_stack.enter_async_context(
                mcp.stdio_client(server_params)
            )
            self.session = await self.exit_stack.enter_async_context(
                mcp.ClientSession(*stdio_transport)
            )
        await self.session.initialize()

    async def list_tools_and_save(self) -> Any:
        """列出并保存工具，返回 MCP ListToolsResult（未初始化抛异常）。"""
        if not self.session:
            raise Exception("MCP Client is not initialized")
        response = await self.session.list_tools()
        self.tools = response.tools
        return response

    def _cancel_connection_task(self, task: asyncio.Task) -> None:
        self._old_connection_tasks = [
            t for t in self._old_connection_tasks if not t.done()
        ]
        if task.done():
            return
        task.cancel()
        self._old_connection_tasks.append(task)

    async def cleanup(self) -> None:
        """清理资源：取消连接任务并关闭 exit stack。"""
        if self._connection_task:
            self._cancel_connection_task(self._connection_task)
            self._connection_task = None
        if self._old_connection_tasks:
            pending = [t for t in self._old_connection_tasks if not t.done()]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            self._old_connection_tasks.clear()
        self.session = None


class MCPTool(FunctionTool):
    """MCP 工具（真实实现，对齐原版 astrbot.core.agent.mcp_client.MCPTool）。

    由 mcp_tool（MCP SDK Tool）与 mcp_client 构造，call 时经 session 调用。
    """

    def __init__(
        self,
        mcp_tool: Any = None,
        mcp_client: Any = None,
        mcp_server_name: str = "",
    ) -> None:
        tool_name = getattr(mcp_tool, "name", "") or ""
        llm_tool_name = re.sub(r"[^A-Za-z0-9_-]+", "_", tool_name)
        description = getattr(mcp_tool, "description", "") or ""
        parameters = getattr(mcp_tool, "inputSchema", None) or getattr(
            mcp_tool, "parameters", None
        ) or {}
        if not isinstance(parameters, dict):
            parameters = {}
        super().__init__(name=llm_tool_name, description=description, parameters=parameters)
        self.mcp_tool: Any = mcp_tool
        self.mcp_server_name: str = mcp_server_name or ""
        self.mcp_client: Any = mcp_client

    async def call(self, context: Any, **kwargs: Any) -> Any:
        """执行 MCP 工具调用，结果转文本；失败返回错误字符串不抛异常。"""
        client = self.mcp_client
        session = getattr(client, "session", None)
        if not session:
            return f"error: MCP 工具 {self.name} 不可用（会话未初始化）"
        try:
            result = await session.call_tool(name=self.mcp_tool.name, arguments=kwargs)
        except Exception as exc:  # noqa: BLE001  失败返回错误字符串
            logger.warning("MCP 工具 %s 调用失败: %s", self.name, exc)
            return f"error: MCP 工具 {self.name} 调用失败: {exc!s}"
        is_error = bool(getattr(result, "isError", False))
        parts: list[str] = []
        for content in getattr(result, "content", None) or []:
            text = getattr(content, "text", None)
            if isinstance(text, str):
                parts.append(text)
        text = "\n".join(parts)
        if is_error:
            text = f"error: {text}".strip()
        return text or str(result)


class _PermissionGuardedTool(FunctionTool):
    """工具权限守卫：call() 前按工具名查宿主 tool_permissions 配置校验。

    宿主桥无专用工具权限 RPC，这里经 get_config_async 读取插件配置中的
    tool_permissions（仪表盘可配置 admin-only 工具）；配置缺失或查询
    失败时放行（保持原透传行为）。
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

    async def _check_tool_permission(self, context: Any) -> bool:
        """按工具名查宿主 tool_permissions 配置校验，未配置/查询失败放行。"""
        try:
            from astrbot._bridge.host import get_bridge

            bridge = get_bridge()
            cfg = await bridge.get_config_async(bridge.plugin_name or "")
        except Exception as e:
            logger.debug(f"tool permission 查询失败，放行: {e}")
            return True
        permissions = (cfg or {}).get("tool_permissions") or {}
        level = permissions.get(self.name)
        if isinstance(level, dict):
            level = level.get("permission")
        if not level:
            return True
        from astrbot.core.star.filter.permission import PermissionType, PermissionTypeFilter

        flag = getattr(PermissionType, str(level).upper(), None)
        if flag is None:
            return True
        return PermissionTypeFilter(flag).filter(context, None)

    async def call(self, context: Any, **kwargs: Any) -> Any:
        """先校验权限（无权限返回错误结果），再透传执行被包装工具。"""
        if not await self._check_tool_permission(context):
            logger.warning(f"工具 {self.name} 无权限调用，拒绝执行")
            return f"error: 工具 {self.name} 无权限调用"
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
    """加载 AstrBot 内置工具（转发 tools.registry 真实现）。

    与 tools/registry.py 同名符号保持同一语义（红线：同名同义），
    避免插件从本模块 import 到恒空版本。
    """
    from astrbot.core.tools.registry import (
        ensure_builtin_tools_loaded as _ensure,
    )

    return await _ensure()


def get_builtin_tool_class(name: str) -> type | None:
    """按名称取内置工具类（转发 tools.registry 真实现）。"""
    from astrbot.core.tools.registry import get_builtin_tool_class as _get

    return _get(name)


def get_builtin_tool_name(tool_cls: type) -> str | None:
    """按类取内置工具名（转发 tools.registry 真实现）。"""
    from astrbot.core.tools.registry import get_builtin_tool_name as _name

    return _name(tool_cls)


def iter_builtin_tool_classes():
    """迭代所有内置工具类（转发 tools.registry 真实现）。"""
    from astrbot.core.tools.registry import iter_builtin_tool_classes as _iter

    return _iter()


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
        # MCP 服务运行期状态（只读视图来源；宿主 MCP 由宿主原生管理，
        # 插件侧通常不主动连接，此处默认空表，插件自建 MCPClient 不入表）。
        self._mcp_server_runtimes: dict[str, Any] = {}
        self._mcp_client_dict_view = _MCPClientDictView(self._mcp_server_runtimes)
        self._mcp_server_runtime_view: Mapping[str, Any] = MappingProxyType(
            self._mcp_server_runtimes
        )

    @property
    def mcp_client_dict(self) -> Mapping[str, Any]:
        """只读的 MCP 客户端映射视图（对齐本体 mcp_client_dict）。"""
        return self._mcp_client_dict_view

    @property
    def mcp_server_runtime_view(self) -> Mapping[str, Any]:
        """只读的 MCP 服务运行期元数据视图（对齐本体）。"""
        return self._mcp_server_runtime_view

    @property
    def mcp_server_runtime(self) -> Mapping[str, Any]:
        """向后兼容的只读运行期视图（对齐本体已弃用的别名）。"""
        return self._mcp_server_runtime_view

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

    # ── 对齐原版 FuncCall 方法名（兼容别名）──────────────────────────
    def get_func(self, name) -> FuncTool | None:
        """按名称取工具（优先返回已激活的工具，对齐原版 get_func 语义）。"""
        for f in reversed(self.func_list):
            if f.name == name and getattr(f, "active", True):
                return f
        for f in reversed(self.func_list):
            if f.name == name:
                return f
        return None

    def empty(self) -> bool:
        """工具注册表是否为空。"""
        return len(self.func_list) == 0

    def activate_llm_tool(self, name: str, star_map: dict | None = None) -> bool:
        """启用指定 LLM 工具（对齐原版 activate_llm_tool 签名）。"""
        func_tool = self.get_func(name)
        if func_tool is None:
            return False
        if star_map and getattr(func_tool, "handler_module_path", None) in star_map:
            meta = star_map[getattr(func_tool, "handler_module_path")]
            if getattr(meta, "activated", True) is False:
                raise ValueError(
                    f"此函数调用工具所属的插件已被禁用，请先在管理面板启用再激活此工具。",
                )
        func_tool.active = True
        return True

    def deactivate_llm_tool(self, name: str) -> bool:
        """禁用指定 LLM 工具（对齐原版 deactivate_llm_tool 签名）。"""
        func_tool = self.get_func(name)
        if func_tool is None:
            return False
        func_tool.active = False
        return True

    def get_full_tool_set(self) -> ToolSet:
        """获取完整工具集（对齐原版 get_full_tool_set 语义，无权限守卫包装）。"""
        tool_set = ToolSet()
        for tool in self.func_list:
            tool_set.add_tool(tool)
        return tool_set

    def is_builtin_tool(self, name: str) -> bool:
        """判断是否为宿主内置工具（对齐原版 is_builtin_tool 语义）。"""
        from astrbot.core.tools.registry import get_builtin_tool_class

        try:
            return get_builtin_tool_class(name) is not None
        except Exception:
            return False

    def _default_permission(self, tool_name: str) -> str:
        """计算非内置工具的兜底权限（对齐本体同名方法）。

        所有非内置工具默认 ``"member"``（不限制）；内置工具不走本方法。
        """
        return "member"

    async def _check_tool_permission(
        self,
        tool_name: str,
        context: Any,
    ) -> str | None:
        """校验工具调用权限（签名对齐本体）。

        无权限时返回错误字符串，有权限返回 None。权限解析自
        SharedPreferences 的 ``tool_permissions``（global 作用域，仪表盘
        可配置 admin-only 工具）；无显式配置时继承 ``_default_permission``
        的兜底值（member → 放行）。
        """
        try:
            perms_raw = await sp.global_get("tool_permissions", {})
        except Exception:
            perms_raw = {}
        defaults = perms_raw.get("_default", {}) if isinstance(perms_raw, dict) else {}
        effective = defaults.get(tool_name)
        if effective is None:
            effective = self._default_permission(tool_name)

        if isinstance(effective, dict):
            effective = effective.get("permission")
        if effective != "admin":
            return None  # member 或未知值 → 放行

        try:
            event = context.context.event
        except AttributeError:
            event = None
        if event is None or not event.is_admin():
            sender_id = getattr(event, "get_sender_id", lambda: "unknown")()
            return (
                f"error: Permission denied. The tool '{tool_name}' requires admin "
                f"privileges. Your ID: {sender_id}. "
                "Ask admin to configure in WebUI → Extension → Components."
            )
        return None

    def get_builtin_tool(self, tool) -> "FuncTool":
        """按名称/类获取宿主内置工具（SDK 薄壳：宿主原生化，未注册抛 KeyError）。"""
        from astrbot.core.tools.registry import (
            get_builtin_tool_class as _cls,
            get_builtin_tool_name as _name,
        )

        tool_cls = None
        if isinstance(tool, str):
            tool_cls = _cls(tool)
            if tool_cls is None:
                raise KeyError(f"Builtin tool {tool} is not registered.")
        elif isinstance(tool, type):
            if _name(tool) is None:
                raise KeyError(
                    f"Builtin tool class {tool.__module__}.{tool.__name__} is not registered.",
                )
            tool_cls = tool
        else:
            raise TypeError("tool must be a builtin tool name or FunctionTool class.")
        return tool_cls()  # type: ignore

    def iter_builtin_tools(self) -> list["FuncTool"]:
        """遍历宿主内置工具实例列表（对齐本体 iter_builtin_tools）。"""
        from astrbot.core.tools.registry import iter_builtin_tool_classes

        return [self.get_builtin_tool(cls) for cls in iter_builtin_tool_classes()]

    # ── MCP 生命周期（宿主 MCP 原生管理；SDK 薄壳保证签名/调用不抛错）───────
    @property
    def mcp_config_path(self) -> str:
        """MCP 配置文件路径（对齐本体：数据目录下 mcp_server.json）。

        本体为 property（不带括号访问），插件按
        ``manager.mcp_config_path`` 读取路径。
        """
        return os.path.join(get_astrbot_data_path(), "mcp_server.json")

    def load_mcp_config(self) -> dict:
        """读取 MCP 配置（SDK 薄壳：文件缺失时返回空结构，不写盘）。"""
        try:
            with open(self.mcp_config_path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {"mcpServers": {}}
        except (OSError, ValueError):
            return {"mcpServers": {}}

    def save_mcp_config(self, config: dict) -> bool:
        """保存 MCP 配置（SDK 薄壳：宿主配置由宿主管理，此处仅返回 True）。"""
        return True

    async def init_mcp_clients(
        self, raise_on_all_failed: bool = False
    ) -> "MCPInitSummary":
        """从 mcp_server.json 初始化 MCP 服务（SDK 薄壳：宿主原生管理）。

        宿主 MCP 服务由宿主侧连接管理；此处不主动连接，返回空摘要。
        """
        return MCPInitSummary()

    @staticmethod
    async def test_mcp_server_connection(config: dict) -> list[str]:
        """测试 MCP 服务器连接并返回工具名列表（对齐本体签名与语义）。

        连接失败时抛 Exception（对齐本体：url 探测失败 / 连接失败均抛出）。
        """
        if "url" in config:
            success, error_msg = await _quick_test_mcp_connection(config)
            if not success:
                raise Exception(error_msg)

        mcp_client = MCPClient()
        try:
            logger.debug(f"testing MCP server connection with config: {config}")
            connected = await mcp_client.connect_to_server(config, "test")
            if not connected:
                raise Exception(f"MCP server connection failed: {config.get('url', '')}")
            tools_res = await mcp_client.list_tools_and_save()
            tool_names = [tool.name for tool in tools_res.tools]
        finally:
            logger.debug("Cleaning up MCP client after testing connection.")
            await mcp_client.cleanup()
        return tool_names

    async def sync_modelscope_mcp_servers(self, access_token: str) -> None:
        """从 ModelScope 平台同步 MCP 服务器配置（SDK 薄壳：宿主原生管理）。

        本体经 ModelScope openapi 拉取服务器列表并写回 mcp_server.json
        后逐个 enable；SDK 的 MCP 服务由宿主 Go 侧管理，此处仅记录日志
        并返回（面板功能，插件不依赖）。
        """
        logger.info(
            "sync_modelscope_mcp_servers: SDK 下 MCP 配置由宿主管理，跳过同步。"
        )

    async def enable_mcp_server(
        self,
        name: str,
        config: dict,
        shutdown_event: "asyncio.Event | None" = None,
        timeout: "float | int | str | None" = None,
    ) -> None:
        """启用一个 MCP 服务（SDK 薄壳：宿主原生管理，不在此连接）。"""

    async def disable_mcp_server(
        self,
        name: str | None = None,
        timeout: float = 10,
    ) -> None:
        """停用一个/全部 MCP 服务（SDK 薄壳：宿主原生管理，不在此连接）。"""

    async def activate_llm_tool_async(self, name: str, star_map: dict) -> bool:
        """异步启用 LLM 工具（对齐本体，内部复用 activate_llm_tool）。"""
        return self.activate_llm_tool(name, star_map)

    async def deactivate_llm_tool_async(self, name: str) -> bool:
        """异步禁用 LLM 工具（对齐本体，内部复用 deactivate_llm_tool）。"""
        return self.deactivate_llm_tool(name)

    def get_func_desc_openai_style(
        self, omit_empty_parameter_field: bool = False
    ) -> list:
        """把全部工具转成 OpenAI 函数描述（对齐原版 get_func_desc_openai_style）。"""
        out = []
        for f in self.list_funcs(only_active=True):
            schema = f.to_schema()
            fn = schema.get("function", schema)
            if omit_empty_parameter_field and not (fn.get("parameters") or {}).get("properties"):
                fn.pop("parameters", None)
            out.append(schema)
        return out

    def get_func_desc_anthropic_style(self) -> list:
        """把全部工具转成 Anthropic 工具描述（对齐原版签名）。"""
        return [
            {
                "name": f.name,
                "description": f.description or "",
                "input_schema": f.parameters or {"type": "object", "properties": {}},
            }
            for f in self.list_funcs(only_active=True)
        ]

    def get_func_desc_google_genai_style(self) -> dict:
        """把全部工具转成 Google GenAI 工具描述（对齐原版签名）。"""
        return {
            "functionDeclarations": [
                {"name": f.name, "description": f.description or "", "parameters": f.parameters or {}}
                for f in self.list_funcs(only_active=True)
            ]
        }


llm_tools = FunctionToolManager()
"""全局 LLM 函数工具注册表"""

# 别名（对齐原版 func_tool_manager.py 末尾的 FuncCall = FunctionToolManager）。
FuncCall = FunctionToolManager
