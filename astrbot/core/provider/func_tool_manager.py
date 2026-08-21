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
