"""PluginService 实现：宿主 RPC → Python 插件 handler。"""
from __future__ import annotations

import inspect
import json
import grpc
import logging

# P1 协议版本：Event/Component/Chain 原生 protobuf data plane（0 JSON）。
# SDK 与 Host 必须一致，Register 握手不匹配则明确失败。
P1_PROTOCOL_VERSION = 2
import threading
import time
from functools import lru_cache

from astrbot._bridge import loop
from astrbot._bridge.gen import plugin_pb2, plugin_pb2_grpc
from astrbot._bridge.serialize import result_to_components
from astrbot.core.message.message_event_result import (
    EventResultType,
    MessageEventResult,
)
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import (
    LLMResponse,
    PluginError,
    ProviderRequest,
    ToolCall,
)
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.custom_filter import CustomFilter
from astrbot.core.star.filter.permission import PermissionType, PermissionTypeFilter
from astrbot.core.star.star_handler import (
    EventType,
    is_virtual_handler,
    star_handlers_registry,
)

logger = logging.getLogger("astrbot.dispatch")

HANDLER_TIMEOUT = 60.0

# EventType → 宿主 hook event 名（不桥接到 star 的除外）
EVENT_TYPE_TO_HOOK: dict[EventType, str] = {
    EventType.OnLLMRequestEvent: "on_llm_request",
    EventType.OnLLMResponseEvent: "on_llm_response",
    EventType.OnAgentBeginEvent: "on_agent_begin",
    EventType.OnAgentDoneEvent: "on_agent_done",
    EventType.OnAfterMessageSentEvent: "on_after_message_sent",
    EventType.OnPluginErrorEvent: "on_plugin_error",
    EventType.OnPluginLoadedEvent: "on_plugin_loaded",
    EventType.OnPluginUnloadedEvent: "on_plugin_unloaded",
    EventType.OnAstrBotLoadedEvent: "on_astrbot_loaded",
    EventType.OnPlatformLoadedEvent: "on_platform_loaded",
    EventType.OnWaitingLLMRequestEvent: "on_waiting_llm_request",
    EventType.OnUsingLLMToolEvent: "on_using_llm_tool",
    EventType.OnLLMToolRespondEvent: "on_llm_tool_respond",
    EventType.OnDecoratingResultEvent: "on_decorating_result",
}

# 由 pipeline 直调、不经 HandleHook 的钩子
DIRECT_CALLED_HOOKS = {"on_llm_request"}

# aiocqhttp 兼容层的桥接钩子：插件实例化了 CQHttp（装饰器事件循环）时，注册
# 一个 on_message 占位钩子，宿主即会把原始 OneBot 事件推给本进程（HandleHook），
# 再经 aiocqhttp.dispatch 分发给 @bot.on_message 等装饰器。
AIOCQHTTP_BRIDGE_HOOK = "__aiocqhttp_bridge__"

# botpy 兼容层的桥接钩子：插件实例化了 Client（装饰器事件循环）时注册，宿主
# 把序列化 AstrMessageEvent 推给 HandleHook，再经 botpy.dispatch 分发到
# @on_message / @on_at_message 等装饰器。
BOTPY_BRIDGE_HOOK = "__botpy_bridge__"

# telegram 兼容层的桥接钩子：插件实例化了 Application 时注册，宿主把序列化
# AstrMessageEvent 推给 HandleHook，再经 telegram.dispatch 分发到
# Application.add_handler 注册的 handler。
TELEGRAM_BRIDGE_HOOK = "__telegram_bridge__"


def _call(handler, *args, timeout: float = HANDLER_TIMEOUT, **kwargs):
    """调用 handler（async / async-generator / sync），返回收集的结果列表。"""
    result = handler(*args, **kwargs)
    if inspect.isasyncgen(result):
        return _consume_asyncgen(result, timeout)
    if inspect.iscoroutine(result):
        try:
            return [loop.run_coro(result, timeout=timeout)]
        except Exception as e:
            logger.error(f"handler {handler.__name__} 执行失败: {e}")
            raise
    return [result]


def _consume_asyncgen(agen, timeout: float):
    out: list = []

    async def _consume():
        async for item in agen:
            out.append(item)
        return out

    try:
        loop.run_coro(_consume(), timeout=timeout)
    except Exception as e:
        logger.error(f"async generator handler 执行失败: {e}")
        raise
    return out


def _bind(handler, inst):
    """把未绑定方法绑定到插件实例。"""
    if inst is not None and inspect.ismethod(handler):
        return handler
    if inst is not None and inspect.isfunction(handler):
        first = None
        try:
            first = next(iter(inspect.signature(handler).parameters))
        except Exception as e:
            logger.debug(f"handler 签名解析失败: {e}")
        if first in ("self", "cls") or first is None:
            bound = getattr(inst, handler.__name__, None)
            if bound is not None and inspect.ismethod(bound):
                return bound
    return handler


def _set_result(resp, event, stop: bool = False, handled: bool = True) -> None:
    """填充响应中的 EventResult 复合消息（新宿主统一从 result 读），并保持
    旧字段赋值不变（旧宿主只读 stop/sent/handled 顶层字段）。新旧宿主都能
    正确读取；result 字段不存在/未赋值时宿主回退到旧字段。"""
    resp.result.handled = bool(handled)
    resp.result.sent = bool(getattr(event, "_has_send_oper", False))
    resp.result.stop_propagation = bool(stop)


def _fit_hook_args(handler, event=None, payload=None):
    """按 handler 声明的参数个数截断传参（对齐 Python 本体语义：
    on_astrbot_loaded/on_platform_loaded 无参，on_plugin_loaded 传 metadata，
    on_llm_response 传 event+response 等）。多余参数不传，避免
    "takes N positional arguments but M were given"。"""
    n = _count_positional(handler)
    args = []
    if n >= 1 and event is not None:
        args.append(event)
    if n >= 2 and payload is not None:
        args.append(payload)
    return args


def _count_positional(handler) -> int:
    """统计 handler 可接受的位置参数个数（对齐本体 call_event_hook 语义）。"""
    try:
        sig = inspect.signature(handler)
        return sum(
            1
            for p in sig.parameters.values()
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        )
    except (ValueError, TypeError):
        return 1


class _ToolTContext:
    """原版 ContextWrapper.context 的 TContext 载体。

    原版（astrbot-py）插件在工具实现里经 ``context.context.event`` 取事件
    （如 livingmemory 的记忆检索工具），桥侧没有 agent 运行时，无法构造完整
    TContext——仅挂 ``.event`` 满足该访问路径，其余字段由插件自行兜底。
    """

    def __init__(self, event):
        self.event = event


def _tool_first_arg_is_context(handler) -> bool:
    """判断工具 handler 首参是否为原版 ContextWrapper 语境（call 型）。

    原版约定 ``FunctionTool.call(context: ContextWrapper, **kwargs)``，插件经
    ``context.context.event`` 取事件；SDK 文档另约定 ``run(event, **kwargs)``
    直接收事件。add_llm_tools 把 run/call 统一抽成 handler 后类型信息丢失，
    桥无法直接区分，只能按签名判定：

    - 首参名 event/e/msg（或注解含 AstrMessageEvent）→ run 型，传事件本身；
    - 首参名 context/ctx/wrapper（或注解含 ContextWrapper/AstrAgentContext）
      → call 型，包一层 ContextWrapper；
    - 签名不可解析时保持旧行为（传事件），不改变既有插件的调用约定。
    """
    try:
        sig = inspect.signature(handler)
        first = next(iter(sig.parameters.values()), None)
    except (ValueError, TypeError):
        return False
    if first is None:
        return False
    name = (first.name or "").lower()
    ann = getattr(first, "annotation", None)
    ann_name = getattr(ann, "__name__", None) or str(ann or "")
    if "AstrMessageEvent" in ann_name:
        return False
    if "ContextWrapper" in ann_name or "AstrAgentContext" in ann_name:
        return True
    if name in ("event", "e", "msg", "message"):
        return False
    return name in ("context", "ctx", "wrapper")


def _hook_args_for_payload(handler, event, payload, event_name=None):
    """按原版签名构造带 payload 钩子的位置参数列表。

    原版（astrbot-py）各钩子是多位置参数，而非单 payload 对象：
    - on_using_llm_tool(event, tool, tool_args)
    - on_llm_tool_respond(event, tool, tool_args, tool_result)
    - on_plugin_error(event, plugin_name, handler_name, error, traceback_text)
    - on_plugin_loaded/on_plugin_unloaded(metadata)
    - on_platform_loaded/on_astrbot_loaded() 无参
    - on_waiting_llm_request/on_after_message_sent(event)
    这里把跨进程 payload（ToolCall/PluginError/LLMResponse/metadata dict）展开
    成对应位置参数，并按 handler 声明数截断（多余不传，缺省参数安全返回）。
    """
    n = _count_positional(handler)
    if n <= 0:
        return []

    # lifecycle 钩子：无 event 概念，按原版语义传参
    if event_name in ("on_plugin_loaded", "on_plugin_unloaded"):
        # 原版 handler(metadata)：仅 metadata 一个位置参数（非 event）
        return [payload][:n] if n >= 1 else []
    if event_name in ("on_platform_loaded", "on_astrbot_loaded"):
        # 原版 handler() 无参
        return []

    # 消息/管线钩子：第一个参数恒为 event
    args = [event]
    from astrbot.core.agent.tool import FunctionTool

    if isinstance(payload, ToolCall):
        tool = FunctionTool(name=payload.tool_name)
        # on_using_llm_tool / on_llm_tool_respond：(event, tool, tool_args[, tool_result])
        if n >= 2:
            args.append(tool)
        if n >= 3:
            args.append(payload.tool_args)
        if n >= 4:
            from mcp import CallToolResult, TextContent

            if payload.result:
                args.append(CallToolResult(content=[TextContent(text=payload.result)], isError=payload.is_error))
            else:
                args.append(None)
    elif isinstance(payload, PluginError):
        # on_plugin_error(event, plugin_name, handler_name, error, traceback_text)
        if n >= 2:
            args.append(payload.plugin_name)
        if n >= 3:
            args.append(payload.handler_name)
        if n >= 4:
            args.append(payload.error)
        if n >= 5:
            args.append(payload.error)  # 宿主未传 traceback_text：以 error 兜底
    elif isinstance(payload, LLMResponse):
        # on_llm_response(event, response)
        if n >= 2:
            args.append(payload)
    else:
        # 未知 payload（含 None）：回退 event（+payload 若有）
        if n >= 2 and payload is not None:
            args.append(payload)
    return args[:n]


class PluginServiceServicer(plugin_pb2_grpc.PluginServiceServicer):
    def __init__(self, plugin_name: str, plugin_version: str, plugin_desc: str, plugin_author: str, plugin_dir: str = ""):
        self.plugin_name = plugin_name
        self.plugin_version = plugin_version
        self.plugin_desc = plugin_desc
        self.plugin_author = plugin_author
        self.plugin_dir = plugin_dir
        self.web_apis: list[tuple] = []  # (route, handler, methods, desc) 由 server 注入
        # plugin_id -> Star 实例（loader 填充）
        self.inst: object | None = None
        # 命令注册：完整命令名/别名 -> (CommandFilter, handler)
        self.commands: dict[str, tuple[CommandFilter, object]] = {}
        self.filter_handlers: list[tuple[str, object, object]] = []  # (name, handler, inst)
        self.hook_handlers: dict[str, tuple[str, object, object]] = {}  # name -> (event, handler, inst)
        self.tools: dict[str, tuple[str, object]] = {}  # tool_name -> (func, inst)
        # 生命周期状态机（取代 _ready/_registered/_instanced 三个 Event）：
        # REGISTERING（等插件 import 完成，放行 Register）→ REGISTERED（宿主
        # Register 完成，身份绑定）→ RUNNING（实例化完成，放行 RPC）。语义
        # 与原门闩一致，错误消息带 expected/actual 便于诊断。
        from astrbot._bridge.state import LifecycleStateMachine

        self.lifecycle = LifecycleStateMachine()
        # Register RPC 内 `self._ready.wait(timeout=120)` 保持原语义（机器
        # wait 的默认 min_state 即 REGISTERING）。
        self._ready = self.lifecycle

    def mark_ready(self) -> None:
        self.lifecycle.mark_ready()

    def mark_registered(self) -> None:
        self.lifecycle.mark_registered()

    def wait_registered(self, timeout: float) -> bool:
        return self.lifecycle.wait_registered(timeout)

    def mark_instanced(self) -> None:
        self.lifecycle.mark_instanced()

    def _wait_instanced(self) -> bool:
        # 实例化就绪等待：必须小于宿主的 RPC 超时（pluginHookRPCTimeout
        # = 30s），否则实例化失败/卡住的插件会让钩子/命令 RPC 被宿主
        # 先超时（DeadlineExceeded 噪音）。15s 内实例化完成即放行；未
        # 完成（失败/慢）快速返回 False，宿主跳过该插件。
        return self.lifecycle._wait_instanced(timeout=15.0)

    # ---- 注册收集 ----
    def _load_config_schema(self) -> dict:
        """读取插件目录 _conf_schema.json → 宿主 FlatSchema 期望的
        {"type":"object","properties":{key:item}} 格式（item 为
        AstrBot 类型描述：type/description/hint/default/slider/options）。"""
        import os

        if not self.plugin_dir:
            return {}
        for name in ("_conf_schema.json", "config_schema.json"):
            path = os.path.join(self.plugin_dir, name)
            if not os.path.exists(path):
                continue
            try:
                with open(path, encoding="utf-8-sig") as f:
                    data = json.load(f)
                if isinstance(data, dict) and data:
                    return {"type": "object", "properties": data}
            except Exception as e:
                logger.warning(f"读取 {name} 失败: {e}")
            break
        return {}

    def build_registry(self) -> None:
        from astrbot.core.star.filter.event_message_type import EventMessageTypeFilter
        from astrbot.core.star.filter.platform_adapter_type import PlatformAdapterTypeFilter
        from astrbot.core.star.filter.regex import RegexFilter

        # aiocqhttp 装饰器事件循环：插件实例化了 CQHttp 时注入 on_message
        # 占位钩子，宿主把原始 OneBot 事件推给 HandleHook 再分发给装饰器。
        try:
            from aiocqhttp import _registry as _cqhttp_registry

            if _cqhttp_registry.has_any():
                self.hook_handlers[AIOCQHTTP_BRIDGE_HOOK] = (
                    "on_message",
                    None,
                    None,
                )
        except Exception as e:
            logger.debug(f"aiocqhttp 桥接钩子注册失败: {e}")

        for md in star_handlers_registry.all():
            if is_virtual_handler(md):
                # 宿主全局命令的虚拟条目：仅供 helps 类插件遍历注册表收集
                # 指令，不注册进 commands/filters（管线不得匹配到无真实
                # handler 的虚拟命令）。
                continue
            inst = self.inst
            handler = _bind(md.handler, inst)
            if md.event_type == EventType.OnCallingFuncToolEvent:
                # llm_tool 注册的 handler（函数对象）
                continue
            if md.event_type == EventType.AdapterMessageEvent:
                is_command = False
                for f in md.event_filters:
                    if isinstance(f, CommandFilter):
                        is_command = True
                        f.init_handler_md(md)
                        for full_name in f.get_complete_command_names():
                            self.commands[full_name] = (f, handler)
                if is_command:
                    continue
                # 过滤器（regex / event_message_type / platform_adapter_type / permission / custom）
                if any(
                    isinstance(f, (RegexFilter, EventMessageTypeFilter, PlatformAdapterTypeFilter, PermissionTypeFilter, CustomFilter))
                    for f in md.event_filters
                ):
                    self.filter_handlers.append((md.handler_full_name, handler, inst))
                continue
            event = EVENT_TYPE_TO_HOOK.get(md.event_type)
            if event:
                self.hook_handlers[md.handler_full_name] = (event, handler, inst)

        # llm_tools
        from astrbot.core.provider.func_tool_manager import llm_tools

        for tool in llm_tools.list_funcs(only_active=True):
            self.tools[tool.name] = (tool, self.inst)

    # ---- RPC 实现 ----
    def Register(self, request, context) -> plugin_pb2.RegisterResponse:
        # 等待插件加载完成（star 注册表填充），避免注册出空 handler 集。
        logger.info(f"Register: 等待插件加载完成（ready 门闩）… plugin_name={self.plugin_name}")
        if not self._ready.wait(timeout=120):
            logger.error(
                f"Register: 插件 {self.plugin_name} 120s 内未完成加载，"
                f"将注册空 handler 集（请检查 __init__/initialize 是否卡死）"
            )
        logger.info(f"Register: 插件已就绪，构建注册表… plugin_name={self.plugin_name}")
        t0 = time.monotonic()
        self.build_registry()
        logger.info(f"Register: 注册表构建完成（{time.monotonic()-t0:.2f}s, "
                    f"{len(self.commands)} 命令 / {len(self.filter_handlers)} 过滤器 / {len(self.hook_handlers)} 钩子）")
        # P1 协议协商：Host 上报的 protocol_version 必须与 SDK 一致（P1 移除
        # legacy event_json/chain_json，不匹配无法互操作 → 明确失败提示升级）。
        if request.protocol_version != P1_PROTOCOL_VERSION:
            context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                f"protocol version mismatch: Host={request.protocol_version} "
                f"SDK(P1)={P1_PROTOCOL_VERSION}; please upgrade the SDK or Host "
                f"to the same protocol version",
            )
        # 注册完成后注入宿主全局命令为虚拟 handler（helps 类插件跨进程枚举
        # 全部插件指令；一次性注入，0 运行期开销，命令随插件重载/宿主重启
        # 更新）。数据源为现有 GetPluginRegistry 通道（宿主每插件带 commands）。
        self._inject_host_commands()
        resp = plugin_pb2.RegisterResponse(
            name=self.plugin_name,
            version=self.plugin_version,
            description=self.plugin_desc,
            author=self.plugin_author,
            config_schema_json=json.dumps(self._load_config_schema()).encode(),
            protocol_version=P1_PROTOCOL_VERSION,
        )
        seen = set()
        for full_name, (f, _) in self.commands.items():
            cmd_name = f.command_name if full_name == f.command_name else full_name
            if cmd_name in seen:
                continue
            seen.add(cmd_name)
            desc = ""
            perm = "everyone"
            if f.handler_md is not None:
                desc = (f.handler_md.desc or "").strip()
                for ef in f.handler_md.event_filters:
                    if isinstance(ef, PermissionTypeFilter) and ef.permission_type == PermissionType.ADMIN:
                        perm = "admin"
            # 父子关系上报：f 恒为 CommandFilter（build_registry 只收
            # CommandFilter，指令组本身不会进入 self.commands）。CommandFilter
            # 上没有 parent_group 引用，用 parent_command_names（父指令组的
            # 完整命令名列表，顶层为 [""]）推导：首个非空完整名的最后一段即
            # 直属父组名（嵌套组如 ["outer inner"] → "inner"）。
            parents = getattr(f, "parent_command_names", None) or []
            first_parent = parents[0].strip() if parents else ""
            parent_group = first_parent.split()[-1] if first_parent else ""
            resp.commands.append(
                plugin_pb2.CommandDesc(
                    name=cmd_name,
                    aliases=list(f.alias) if f.alias else [],
                    description=desc,
                    permission=perm,
                    parent_group=parent_group,
                    is_sub_command=bool(parent_group),
                )
            )
        for name, _, _ in self.filter_handlers:
            resp.filters.append(plugin_pb2.FilterDesc(name=name))
        for name, (event, _, _) in self.hook_handlers.items():
            resp.hooks.append(plugin_pb2.HookDesc(name=name, event=event))
        from astrbot.core.provider.func_tool_manager import llm_tools

        for tool in llm_tools.list_funcs(only_active=True):
            params = tool.parameters or {"type": "object", "properties": {}}
            if not isinstance(params, dict):
                params = {"type": "object", "properties": {}}
            resp.tools.append(
                plugin_pb2.ToolDesc(
                    name=tool.name,
                    description=tool.description or "",
                    params_json=json.dumps(params).encode(),
                )
            )
        # Web APIs（context.register_web_api）
        for route, _, methods, desc in self.web_apis:
            resp.web_apis.append(
                plugin_pb2.WebApiDesc(route=route, methods=list(methods), description=desc)
            )
        # 宿主 Register 完成（身份绑定已生效）：放行插件实例化。
        self.mark_registered()
        return resp

    def _find_command(self, name: str):
        for full_name, (f, handler) in self.commands.items():
            if full_name == name or f.command_name == name:
                return f, handler
        return None, None

    def _host_bridge_getter(self):
        """返回宿主桥（HostBridge）获取器（惰性，供注册后全局命令注入）。"""
        from astrbot._bridge.host import get_bridge

        return get_bridge

    def _inject_host_commands(self) -> None:
        """从宿主 GetPluginRegistry（现有通道，每插件带 commands）注入全局命令。"""
        try:
            stars = self._host_bridge_getter()().get_plugin_registry()
        except Exception as e:
            logger.debug(f"宿主全局命令注入失败（宿主可能不支持 commands）: {e}")
            return
        commands_by_plugin: dict[str, list[dict]] = {}
        star_meta_by_plugin: dict[str, dict] = {}
        for s in stars:
            if not isinstance(s, dict):
                continue
            pid = str(s.get("id") or "") or str(s.get("name") or "")
            if not pid:
                continue
            star_meta_by_plugin[pid] = s
            cmds = s.get("commands")
            if isinstance(cmds, list):
                commands_by_plugin[pid] = [
                    c for c in cmds if isinstance(c, dict)
                ]
        if commands_by_plugin:
            star_handlers_registry.inject_host_commands(
                commands_by_plugin, star_meta_by_plugin
            )

    def HandleCommand(self, request, context) -> plugin_pb2.HandleCommandResponse:
        if not self._wait_instanced():
            logger.warning("HandleCommand: 插件实例尚未就绪，跳过命令处理")
            return plugin_pb2.HandleCommandResponse()
        # 注意：指令传输路径不触发宿主全局命令同步（零额外开销）——
        # 同步仅在 star 注册表 _handlers 被读取时惰性发生（helps 类插件
        # 渲染帮助图时），见 star_handler._maybe_sync_host_commands。
        f, handler = self._find_command(request.name)
        resp = plugin_pb2.HandleCommandResponse()
        if f is None or handler is None:
            return resp
        try:
            event = AstrMessageEvent.from_proto(request.event)
        except json.JSONDecodeError as e:
            logger.error(f"HandleCommand: 事件解析失败: {e}")
            return resp
        event.is_at_or_wake_command = True

        # 参数转换（对齐 Python validate_and_convert_params；宿主已传拆分后的 args）
        params = {}
        try:
            if request.args:
                params = f.validate_and_convert_params(list(request.args), f.handler_params)
            else:
                params = {}
        except ValueError as e:
            from astrbot.core.utils.error_redaction import safe_error

            resp.text = safe_error("参数错误: ", e)
            _set_result(resp, event, handled=True)
            return resp

        bound = _bind(handler, self.inst)
        try:
            results = _call(bound, event, **params)
        except Exception as e:
            from astrbot.core.utils.error_redaction import safe_error

            logger.error(f"命令 {request.name} 执行失败: {e}")
            resp.text = safe_error("插件执行失败: ", e)
            _set_result(resp, event, handled=True)
            return resp

        comps: list = []
        stop = False
        for r in results:
            if r is None:
                continue
            if isinstance(r, ProviderRequest):
                # request_llm：不设 Result，宿主继续默认 LLM
                continue
            c, s = result_to_components(r)
            comps.extend(c)
            stop = stop or s
        # event.stop_event()（_force_stopped）也要反映到响应：box 等插件
        # 在 handler 里 stop_event() 表示"事件已处理、不要再走 LLM"，但
        # handler 返回值里没有 Result（recall_task 路径），只靠返回值收集
        # stop 会漏掉 → 宿主继续 LLM 兜底（重复回复）。
        stop = stop or event.is_stopped()
        if comps:
            from astrbot._bridge.serialize import component_list_to_proto
            resp.chain.extend(component_list_to_proto(comps))
        resp.stop = stop
        resp.sent = event._has_send_oper
        _set_result(resp, event, stop=stop, handled=True)
        return resp

    def HandleFilter(self, request, context) -> plugin_pb2.HandleFilterResponse:
        if not self._wait_instanced():
            logger.warning("HandleFilter: 插件实例尚未就绪，跳过过滤器")
            return plugin_pb2.HandleFilterResponse(allow=True)
        try:
            event = AstrMessageEvent.from_proto(request.event)
        except json.JSONDecodeError as e:
            logger.error(f"HandleFilter: 事件解析失败: {e}")
            return plugin_pb2.HandleFilterResponse(allow=True)
        for name, handler, inst in self.filter_handlers:
            if name == request.name:
                # 先跑注册的过滤器（regex / 事件类型 / 平台 / 权限 / 自定义），
                # 全部匹配才调用 handler；不匹配直接放行（不调用、不拦截）。
                from astrbot.core.star.filter.command import CommandFilter
                from astrbot.core.star.star_handler import star_handlers_registry

                md = star_handlers_registry.get_handler_by_full_name(request.name)
                if md is None:
                    # 注册表 miss（热重载后注册表快照过时等）：无法证明该事件
                    # 被授权，fail-closed 拒绝而不是放行。
                    logger.error(f"HandleFilter: 找不到 {request.name} 的注册元数据，拒绝")
                    return plugin_pb2.HandleFilterResponse(allow=False)
                cfg = None
                try:
                    cfg = getattr(inst, "config", None)
                except Exception as e:
                    logger.debug(f"读取过滤器配置失败: {e}")
                for f in md.event_filters:
                    if isinstance(f, CommandFilter):
                        continue
                    try:
                        if not f.filter(event, cfg):
                            return plugin_pb2.HandleFilterResponse(allow=True)
                    except Exception as e:
                        # 过滤器抛异常：无法证明事件被授权，fail-closed 拒绝。
                        logger.warning(f"过滤器 {name} 的 {f} 执行异常，拒绝: {e}")
                        return plugin_pb2.HandleFilterResponse(allow=False)
                bound = _bind(handler, self.inst)
                try:
                    results = _call(bound, event)
                except Exception as e:
                    # handler 执行异常保留放行：事件确已进入处理流程，不因
                    # 插件自身故障拦截整个管线。
                    logger.error(f"过滤器 {request.name} 执行失败: {e}")
                    return plugin_pb2.HandleFilterResponse(allow=True)
                allow = True
                for r in results:
                    if r is False:
                        allow = False
                    elif r is True:
                        allow = True
                resp = plugin_pb2.HandleFilterResponse(allow=allow, sent=event._has_send_oper)
                _set_result(resp, event, handled=True)
                return resp
        # 注册表快照过时（热重载后 self.filter_handlers 未包含该过滤器）：
        # 无法证明事件被授权，fail-closed 拒绝。
        logger.error(f"HandleFilter: 未注册过滤器 {request.name}，拒绝")
        return plugin_pb2.HandleFilterResponse(allow=False)

    def FeedSessionWait(self, request, context) -> plugin_pb2.FeedSessionWaitResponse:
        """宿主推送“插件注册了等待的 umo 的入站消息”（session_waiter 跨进程
        喂入）。反序列化事件后经 try_trigger 匹配 USER_SESSIONS 中的等待会话
        并触发其 handler；无匹配返回 handled=False。"""
        try:
            event = AstrMessageEvent.from_proto(request.event)
        except json.JSONDecodeError as e:
            logger.error(f"FeedSessionWait: 事件解析失败: {e}")
            return plugin_pb2.FeedSessionWaitResponse(handled=False)
        if not event:
            return plugin_pb2.FeedSessionWaitResponse(handled=False)
        try:
            # try_trigger 是 async 且触碰常驻 loop 上的异步状态（waiter 的
            # future/_lock），必须在常驻 loop 上执行：与 _call 一致走
            # loop.run_coro（run_coroutine_threadsafe），而非 asyncio.run——
            # gRPC handler 线程上直接 asyncio.run 会与常驻 loop 并发竞争。
            from astrbot.core.utils.session_waiter import try_trigger

            handled = bool(loop.run_coro(try_trigger(event), timeout=30.0))
            return plugin_pb2.FeedSessionWaitResponse(handled=handled)
        except Exception as e:
            logger.error(f"FeedSessionWait 分发失败: {e}")
            return plugin_pb2.FeedSessionWaitResponse(handled=False)

    def FeedCronJob(self, request, context) -> plugin_pb2.FeedCronJobResponse:
        """宿主推送“插件 basic 定时任务到点触发”（cron 跨进程喂入）。解析
        payload 后经 cron_manager 的模块级喂入入口按 job_id 匹配宿主转发的
        任务记录（handler 保存在本地）并调度执行；无匹配 handler 返回
        handled=False（对齐 FeedSessionWait 语义）。handler 派发后立即返回，
        不在本 RPC 内等待执行完成（宿主超时 60s）。"""
        try:
            payload = json.loads(request.payload_json) if request.payload_json else {}
            if not isinstance(payload, dict):
                payload = {}
        except json.JSONDecodeError as e:
            logger.error(f"FeedCronJob: payload 解析失败: {e}")
            return plugin_pb2.FeedCronJobResponse(handled=False)
        try:
            from astrbot.core.utils.cron_manager import feed_cron_job

            handled = bool(
                feed_cron_job(request.job_id, request.job_name, payload, request.run_at)
            )
            return plugin_pb2.FeedCronJobResponse(handled=handled)
        except Exception as e:
            logger.error(f"FeedCronJob 分发失败: {e}")
            return plugin_pb2.FeedCronJobResponse(handled=False)

    def GetConfigSchema(self, request, context) -> plugin_pb2.GetConfigSchemaResponse:
        """宿主实时拉取插件当前配置 schema（对齐 Python AstrBot：WebUI 配置
        对话框读取运行中 star 实例的 config.schema）。update_manager 等插件在
        __init__ 动态填充插件列表选项（options/labels），这些值不在 Register
        静态快照里，须实时返回。取不到时返回空 bytes，宿主回退 Register 快照。"""
        try:
            inst = self.inst
            cfg = getattr(inst, "config", None)
            schema = getattr(cfg, "schema", None)
            if isinstance(schema, dict):
                return plugin_pb2.GetConfigSchemaResponse(
                    schema_json=json.dumps(schema).encode()
                )
        except Exception as e:
            logger.warning(f"GetConfigSchema 获取失败: {e}")
        return plugin_pb2.GetConfigSchemaResponse(schema_json=b"")

    def HandleHook(self, request, context) -> plugin_pb2.HookResponse:
        if not self._wait_instanced():
            logger.warning("HandleHook: 插件实例尚未就绪，跳过钩子分发")
            return plugin_pb2.HookResponse(handled=False)
        resp = plugin_pb2.HookResponse(handled=False)
        # aiocqhttp 桥接钩子：把宿主推来的原始 OneBot 事件分发给 @bot.on_message
        # 等装饰器（该钩子无插件 handler，转发后直接返回）。
        if request.name == AIOCQHTTP_BRIDGE_HOOK:
            try:
                from aiocqhttp import dispatch as _aiocqhttp_dispatch

                try:
                    event = AstrMessageEvent.from_proto(request.event)
                except json.JSONDecodeError as e:
                    logger.error(f"HandleHook(aiocqhttp): 事件解析失败: {e}")
                    return resp
                from astrbot._bridge.serialize import proto_to_event_dict

                event_data = proto_to_event_dict(request.event)
                _aiocqhttp_dispatch(event_data.get("raw_message"))
            except Exception as e:  # noqa: BLE001
                logger.error(f"aiocqhttp 事件分发失败: {e}")
            return resp
        # botpy 桥接钩子：把宿主推来的序列化 AstrMessageEvent 分发给
        # @bot.on_message / @on_at_message 等装饰器（该钩子无插件 handler，
        # 转发后直接返回）。
        if request.name == BOTPY_BRIDGE_HOOK:
            try:
                from botpy import dispatch as _botpy_dispatch

                try:
                    event = AstrMessageEvent.from_proto(request.event)
                except json.JSONDecodeError as e:
                    logger.error(f"HandleHook(botpy): 事件解析失败: {e}")
                    return resp
                from astrbot._bridge.serialize import proto_to_event_dict

                event_data = proto_to_event_dict(request.event)
                _botpy_dispatch(event_data)
            except Exception as e:  # noqa: BLE001
                logger.error(f"botpy 事件分发失败: {e}")
            return resp
        # telegram 桥接钩子：把宿主推来的序列化 AstrMessageEvent 分发给
        # Application.add_handler 注册的 handler。
        if request.name == TELEGRAM_BRIDGE_HOOK:
            try:
                from telegram import dispatch as _telegram_dispatch

                try:
                    event = AstrMessageEvent.from_proto(request.event)
                except json.JSONDecodeError as e:
                    logger.error(f"HandleHook(telegram): 事件解析失败: {e}")
                    return resp
                from astrbot._bridge.serialize import proto_to_event_dict

                event_data = proto_to_event_dict(request.event)
                _telegram_dispatch(event_data)
            except Exception as e:  # noqa: BLE001
                logger.error(f"telegram 事件分发失败: {e}")
            return resp
        entry = self.hook_handlers.get(request.name)
        if entry is None:
            return resp
        event_name, handler, inst = entry
        try:
            event = AstrMessageEvent.from_proto(request.event)
        except json.JSONDecodeError as e:
            logger.error(f"HandleHook {request.name}: 事件解析失败: {e}")
            return resp

        if event_name in ("on_decorating_result", "on_result_handling"):
            from astrbot._bridge.serialize import proto_to_component_list
            from astrbot.core.message.components import Unknown

            # 入站链：原生 proto Component（P1，0 JSON），逐个兜底。
            comps = []
            for c in (request.chain or []):
                try:
                    comps.append(proto_to_component_list([c])[0])
                except Exception as e:
                    logger.warning(f"HandleHook {request.name}: 组件转换失败，回退 Unknown: {e}")
                    comps.append(Unknown(text=""))
            result = MessageEventResult(comps)
            # 对齐 astrbot-py 本体：结果钩子签名是 `handler(event)`（只收 event），
            # 插件内部通过 event.get_result()/event.set_result() 读取/修改结果链
            #（如 meme_manager 的 on_decorating_result(self, event) 原地改
            # result.chain）。先把入站结果预置到 event，再只传 event 调用。
            event.set_result(result)
            bound = _bind(handler, self.inst)
            try:
                results = _call(bound, event)
            except Exception as e:
                logger.error(f"结果钩子 {request.name} 执行失败: {e}")
                return resp
            # 钩子后重新读取 event 结果：插件可能 set_result / 原地改 chain /
            # clear_result（对齐原版 result_decorate 钩子后再 get_result 语义）。
            new_result = event.get_result()
            if new_result is None:
                # 插件清空了结果 → 不回传 chain（对齐原版 cleared message result）
                return resp
            if new_result.chain:
                from astrbot._bridge.serialize import component_list_to_proto
                resp.chain.extend(component_list_to_proto(new_result.chain))
                resp.stop = bool(
                    new_result.result_type == EventResultType.STOP
                ) if isinstance(new_result, MessageEventResult) else False
                resp.handled = True
                _set_result(resp, event, stop=resp.stop, handled=True)
            return resp

        payload = None
        if event_name in ("on_plugin_loaded", "on_plugin_unloaded"):
            # 原版签名 handler(metadata)：metadata 是 StarMetadata（dict）。
            # 宿主 payload_json = {"plugin_name": ...}（子进程跨进程无法给
            # 完整 StarMetadata 对象，传插件名字典即可，对齐 plugin_name 访问）。
            payload = "metadata"
            if request.payload_json:
                try:
                    metadata = json.loads(request.payload_json)
                    if isinstance(metadata, dict):
                        payload = metadata
                except json.JSONDecodeError as e:
                    logger.error(f"HandleHook {request.name}: payload_json 解析失败: {e}")
        elif event_name == "on_llm_response":
            pl = LLMResponse()
            if request.payload_json:
                try:
                    data = json.loads(request.payload_json)
                    pl._completion_text = data.get("text", "")
                except json.JSONDecodeError as e:
                    logger.error(f"HandleHook {request.name}: payload_json 解析失败: {e}")
            payload = pl
        elif event_name in ("on_using_llm_tool", "on_llm_tool_respond"):
            payload = ToolCall()
            if request.payload_json:
                try:
                    data = json.loads(request.payload_json)
                    payload.tool_name = data.get("tool_name", "")
                    payload.tool_args = data.get("tool_args") or {}
                    payload.result = data.get("result", "")
                    payload.is_error = bool(data.get("is_error", False))
                except json.JSONDecodeError as e:
                    logger.error(f"HandleHook {request.name}: payload_json 解析失败: {e}")
        elif event_name == "on_plugin_error":
            payload = PluginError()
            if request.payload_json:
                try:
                    data = json.loads(request.payload_json)
                    payload.plugin_name = data.get("plugin_name", "")
                    payload.handler_name = data.get("handler_name", "")
                    payload.error = data.get("error", "")
                except json.JSONDecodeError as e:
                    logger.error(f"HandleHook {request.name}: payload_json 解析失败: {e}")

        bound = _bind(handler, self.inst)
        try:
            results = _call(bound, *_hook_args_for_payload(bound, event, payload, event_name))
        except Exception as e:
            logger.error(f"钩子 {request.name} ({event_name}) 执行失败: {e}")
            return resp
        for r in results:
            if isinstance(r, MessageEventResult) and r.is_stopped():
                resp.stop = True
        resp.handled = True
        resp.sent = event._has_send_oper
        _set_result(resp, event, stop=resp.stop, handled=True)
        return resp

    def HandleLLMRequest(self, request, context) -> plugin_pb2.HandleLLMRequestResponse:
        if not self._wait_instanced():
            logger.warning("HandleLLMRequest: 插件实例尚未就绪，跳过 LLM 请求钩子")
            return plugin_pb2.HandleLLMRequestResponse(
                system_prompt=request.system_prompt, user_prompt=request.user_prompt
            )
        resp = plugin_pb2.HandleLLMRequestResponse(
            system_prompt=request.system_prompt, user_prompt=request.user_prompt
        )
        entry = self.hook_handlers.get(request.name)
        if entry is None:
            return resp
        _, handler, inst = entry
        try:
            event = AstrMessageEvent.from_proto(request.event)
        except json.JSONDecodeError as e:
            logger.error(f"HandleLLMRequest {request.name}: 事件解析失败: {e}")
            return resp
        req = ProviderRequest(
            prompt=request.user_prompt,
            system_prompt=request.system_prompt,
        )
        bound = _bind(handler, self.inst)
        try:
            results = _call(bound, *_fit_hook_args(bound, event, req))
        except Exception as e:
            logger.error(f"LLM 请求钩子 {request.name} 执行失败: {e}")
            return resp
        for r in results:
            if r is None:
                continue
            if isinstance(r, ProviderRequest):
                req = r
        # 插件可修改 system_prompt 与 prompt（user_prompt），宿主协议
        # HandleLLMRequestResponse.user_prompt 回传修改后的用户提示词。
        resp.system_prompt = req.system_prompt or ""
        resp.user_prompt = req.prompt or ""
        resp.stop = bool(getattr(req, "stop", False))
        resp.sent = event._has_send_oper
        _set_result(resp, event, stop=resp.stop, handled=True)
        return resp

    def HandleTool(self, request, context) -> plugin_pb2.HandleToolResponse:
        if not self._wait_instanced():
            logger.warning("HandleTool: 插件实例尚未就绪，跳过工具调用")
            return plugin_pb2.HandleToolResponse(text="插件未就绪", is_error=True)
        entry = self.tools.get(request.name)
        if entry is None:
            # self.tools 是 Register 时（registry_build 阶段）的快照，而插件
            # 工具在实例化阶段（__init__/initialize 的 add_llm_tools）注册，
            # 晚于 Register——快照里没有。回退到实时注册表（对齐 ListTools 的
            # 运行时收集），避免"工具 X 未找到"。
            from astrbot.core.provider.func_tool_manager import llm_tools

            live = llm_tools.get_func_by_name(request.name)
            if live is None:
                return plugin_pb2.HandleToolResponse(text=f"工具 {request.name} 未找到", is_error=True)
            entry = (live, self.inst)
        tool, inst = entry
        try:
            event = AstrMessageEvent.from_proto(request.event)
        except json.JSONDecodeError as e:
            logger.error(f"HandleTool {request.name}: 事件解析失败: {e}")
            return plugin_pb2.HandleToolResponse(text=f"工具 {request.name} 事件解析失败", is_error=True)
        args = {}
        if request.args_json:
            try:
                args = json.loads(request.args_json)
            except Exception as e:
                logger.warning(f"HandleTool {request.name}: args_json 解析失败: {e}")
                args = {}
            if not isinstance(args, dict):
                args = {}
        handler = tool.handler
        bound = _bind(handler, self.inst)
        try:
            first_arg = event
            if _tool_first_arg_is_context(bound):
                # 原版 call 型工具：首参为 ContextWrapper（context.context.event
                # 取事件）。裸传 event 会让插件 AttributeError（livingmemory
                # recall_long_term_memory 实测炸点），进而以 internal_error 回给 LLM。
                from astrbot.core.agent.run_context import ContextWrapper

                first_arg = ContextWrapper(context=_ToolTContext(event), messages=[])
            results = _call(bound, first_arg, **args)
        except Exception as e:
            from astrbot.core.utils.error_redaction import safe_error

            logger.error(f"工具 {request.name} 执行失败: {e}")
            resp = plugin_pb2.HandleToolResponse(
                text=safe_error(f"工具 {request.name} 执行失败: ", e), is_error=True
            )
            _set_result(resp, event, handled=True)
            return resp
        texts = []
        for r in results:
            if r is None:
                continue
            if isinstance(r, str):
                texts.append(r)
            elif isinstance(r, MessageEventResult):
                from astrbot._bridge.serialize import component_to_json as _c2j

                for c in (r.chain or []):
                    if hasattr(c, "text") and c.type == "Plain":
                        texts.append(c.text)
            elif hasattr(r, "content") and isinstance(r.content, list):
                # mcp CallToolResult（嵌入式 mcp 兼容层）：提取 content 里的
                # TextContent.text（如 Bing 搜索插件的 run 返回值）。
                is_err = bool(getattr(r, "isError", False))
                for c in r.content:
                    if c is None:
                        continue
                    text = getattr(c, "text", None)
                    if isinstance(text, str):
                        # isError=True 时标记为工具错误，宿主/LLM 可据此判断
                        # 该次工具调用失败。
                        texts.append(("[工具错误] " if is_err else "") + text)
        resp = plugin_pb2.HandleToolResponse(
            text="\n".join(t for t in texts if t), sent=event._has_send_oper
        )
        _set_result(resp, event, handled=True)
        return resp

    def HealthCheck(self, request, context) -> plugin_pb2.HealthResponse:
        # ok 反映实例化完成状态：宿主 TriggerHookPayload 推送生命周期钩子
        # （on_platform_loaded 等）前先探测，未实例化（进行中/失败）的插件
        # 被跳过，避免 RPC 超时（实例化失败时 _wait_instanced 等待 15s 仍
        # 会超过宿主 30s 超时的一半，且失败插件推送钩子无意义）。
        from astrbot._bridge.state import LifecycleStateMachine

        ready = self.lifecycle.state() == LifecycleStateMachine.RUNNING
        return plugin_pb2.HealthResponse(ok=ready, version=self.plugin_version)

    def SetLogLevel(self, request, context) -> plugin_pb2.Empty:
        """调整插件子进程的日志级别（宿主 per-plugin 覆盖）。

        level 为空字符串表示跟随宿主全局级别（回到 INFO 兜底）。直接改 root
        logger 级别——插件侧 stderr 的日志行经 Python logging 过滤后才转发
        给宿主，改这里即可实时生效，无需重启插件。
        """
        level = (request.level or "").strip().upper()
        if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            level = "INFO"
        logging.getLogger().setLevel(getattr(logging, level, logging.INFO))
        logger.log(
            getattr(logging, level, logging.INFO),
            f"插件日志级别已调整为 {level}（宿主设置）",
        )
        return plugin_pb2.Empty()

    def ListTools(self, request, context) -> plugin_pb2.ListToolsResponse:
        """返回插件当前注册的 LLM 函数工具。

        插件工具通常在实例化阶段（__init__/initialize 里的
        Context.add_llm_tools）注册，晚于 Register——宿主通过本 RPC 实时拉取
        最新工具列表（对齐 Python AstrBot 的运行时工具收集）。
        """
        from astrbot.core.provider.func_tool_manager import llm_tools

        resp = plugin_pb2.ListToolsResponse()
        for tool in llm_tools.list_funcs(only_active=True):
            params = tool.parameters or {"type": "object", "properties": {}}
            if not isinstance(params, dict):
                params = {"type": "object", "properties": {}}
            resp.tools.append(
                plugin_pb2.ToolDesc(
                    name=tool.name,
                    description=tool.description or "",
                    params_json=json.dumps(params).encode(),
                )
            )
        return resp

    def ListWebApis(self, request, context) -> plugin_pb2.ListWebApisResponse:
        """返回插件当前注册的 Web API 路由。

        路由可能在实例化阶段（__init__/initialize 里的
        context.register_web_api）注册，晚于 Register 快照——宿主通过本
        RPC 实时拉取最新路由表（对齐 ListTools），使运行期注册的 Web API
        无需重启即可被宿主网关路由。
        """
        resp = plugin_pb2.ListWebApisResponse()
        for route, _, methods, desc in self.web_apis:
            resp.web_apis.append(
                plugin_pb2.WebApiDesc(route=route, methods=list(methods), description=desc)
            )
        return resp

    def HandleWebRequest(self, request, context) -> plugin_pb2.HandleWebRequestResponse:
        """宿主 /api/plug/<plugin_path> 网关转发来的 HTTP 请求。

        按插件注册的 route（含动态 <param> 段）匹配，构造 FakeQuartRequest 并
        注入 quart 全局 request（_cv_request），然后执行 handler。
        """
        import re as _re

        path = request.path
        if not path.startswith("/"):
            path = "/" + path
        method = request.method.upper()

        for route, handler, methods, _ in self.web_apis:
            if method not in [m.upper() for m in methods]:
                continue
            pattern, names = self._web_route_pattern(route)
            if pattern is None:
                continue
            m = pattern.match(path)
            if not m:
                continue
            path_params = {n: m.group(n) for n in names if m.groupdict().get(n) is not None}
            try:
                result = self._run_web_handler(handler, request, path_params)
            except Exception as e:
                logger.error(f"Web API {route} 执行失败: {e}")
                import traceback

                logger.debug(traceback.format_exc())
                return plugin_pb2.HandleWebRequestResponse(
                    status_code=500,
                    body=json.dumps(
                        {"status": "error", "message": "internal plugin error"},
                        ensure_ascii=False,
                    ).encode(),
                )
            return self._serialize_web_result(result)
        return plugin_pb2.HandleWebRequestResponse(status_code=404)

    @staticmethod
    @lru_cache(maxsize=64)
    def _web_route_pattern(route: str):
        import re as _re

        route = route if route.startswith("/") else "/" + route
        names: list[str] = []
        parts = []
        for seg in route.split("/"):
            if not seg:
                continue
            m = _re.fullmatch(r"<([^>]+)>", seg)
            if m:
                name = m.group(1)
                names.append(name)
                parts.append(f"(?P<{name}>[^/]+)")
            else:
                parts.append(_re.escape(seg))
        try:
            return _re.compile("^" + "/" + "/".join(parts) + "$"), names
        except _re.error:
            return None, names

    def _run_web_handler(self, handler, req: plugin_pb2.HandleWebRequestRequest, path_params: dict):
        """构造 FakeQuartRequest（对齐 quart 全局 request 接口），注入
        quart._cv_request 与 astrbot.api.web.request 上下文，执行 handler。"""
        from astrbot.api.web import PluginRequest, PluginUploadFile, bind_request_context

        # 拆 query（含 multipart 表单字段已在宿主侧并入 query）
        query_pairs: list[tuple[str, str]] = [(kv.key, kv.value) for kv in req.query]
        headers = {kv.key: kv.value for kv in req.headers}
        files: list[tuple[str, PluginUploadFile]] = [
            (f.field, PluginUploadFile(f.filename, f.content_type, f.content))
            for f in req.files
        ]
        pname = self.plugin_name
        plugin_req = PluginRequest(
            method=req.method,
            path=req.path,
            query=query_pairs,
            headers=headers,
            body=req.body,
            files=files,
            path_params=path_params,
            plugin_name=pname,
        )

        fake = FakeQuartRequest(plugin_req, path_params)
        bound = _bind(handler, self.inst)
        # 路径参数按名解包（Python 本体：view_handler(**path_values)）
        kwargs = dict(path_params)

        async def _run_with_ctx():
            # async handler 的协程在常驻 loop 线程运行（loop.run_coro 的
            # run_coroutine_threadsafe 继承的是 loop 线程的 context，而非
            # gRPC handler 线程的 context），因此 quart/astrbot 的 ContextVar
            # 必须在协程内部（loop 线程上）注入。
            cv_tokens: list[tuple[Any, Any]] = []
            try:
                from quart.globals import _cv_app, _cv_request

                cv_tokens.append((_cv_app, _cv_app.set(FakeQuartAppCtx())))
                cv_tokens.append((_cv_request, _cv_request.set(fake)))
            except Exception as e:
                logger.debug(f"quart 全局上下文注入失败: {e}")
            try:
                with bind_request_context(plugin_req):
                    result = bound(**kwargs)
                    if inspect.isasyncgen(result):
                        out: list = []
                        async for item in result:
                            out.append(item)
                        return out
                    if inspect.iscoroutine(result):
                        return await result
                    return result
            finally:
                for var, token in reversed(cv_tokens):
                    try:
                        var.reset(token)
                    except Exception:
                        pass

        if inspect.iscoroutinefunction(bound) or inspect.isasyncgenfunction(bound):
            return loop.run_coro(_run_with_ctx(), timeout=HANDLER_TIMEOUT)

        # 同步 handler：直接在 gRPC 线程注入执行（协程在 loop 线程运行，
        # 此处注入的 ContextVar 对同步执行可见）。
        cv_tokens: list[tuple[Any, Any]] = []
        try:
            from quart.globals import _cv_app, _cv_request

            cv_tokens.append((_cv_app, _cv_app.set(FakeQuartAppCtx())))
            cv_tokens.append((_cv_request, _cv_request.set(fake)))
        except Exception as e:
            logger.debug(f"quart 全局上下文注入失败: {e}")

        try:
            with bind_request_context(plugin_req):
                results = _call(bound, **kwargs)
                return results[-1] if results else None
        finally:
            for var, token in reversed(cv_tokens):
                try:
                    var.reset(token)
                except Exception:
                    pass

    @staticmethod
    def _serialize_web_result(result):
        """把 handler 返回值转成宿主响应：dict→json、quart Response→
        (status, headers, body)、(resp, status) 元组。"""
        import json as _json

        status = 200
        headers: dict[str, str] = {}
        body: bytes = b""

        if isinstance(result, tuple) and len(result) >= 2 and isinstance(result[1], int):
            status = result[1]
            if len(result) >= 3:
                # quart/flask 三元组 (body, status, headers)：headers 不能丢弃
                raw_headers = result[2]
                if isinstance(raw_headers, dict):
                    headers.update(raw_headers)
                elif isinstance(raw_headers, (list, tuple)):
                    headers.update(dict(raw_headers))
            result = result[0]
        if result is None:
            body = b""
        elif isinstance(result, dict):
            body = _json.dumps(result, ensure_ascii=False).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        elif isinstance(result, str):
            body = result.encode("utf-8")
            headers.setdefault("Content-Type", "text/plain; charset=utf-8")
        elif hasattr(result, "status_code") and hasattr(result, "get_data"):
            # quart Response（jsonify/make_response/send_file 产物）
            # 注意：quart 的 get_data() 是 async（asgi 模型），需同步执行。
            status = int(result.status_code)
            try:
                data = result.get_data()
                if inspect.iscoroutine(data):
                    # 与常驻 loop 冲突：不能 asyncio.run（gRPC handler 线程上
                    # 会与常驻 loop 并发竞争），走 loop.run_coro。
                    data = loop.run_coro(data, timeout=30)
                body = data if isinstance(data, bytes) else str(data).encode()
            except Exception as e:
                logger.debug(f"Web 结果 get_data 失败，回退 body: {e}")
                body = getattr(result, "body", b"")
            for k, v in getattr(result, "headers", {}).items():
                headers[str(k)] = str(v)
        else:
            body = str(result).encode("utf-8")
        resp = plugin_pb2.HandleWebRequestResponse(status_code=status, body=body)
        for k, v in headers.items():
            resp.headers.append(plugin_pb2.WebKV(key=k, value=v))
        return resp

    def Cleanup(self, request, context) -> plugin_pb2.Empty:
        from astrbot._bridge.loader import terminate_plugin
        from astrbot.core.star.star import star_map

        for md in star_map.values():
            if md.star_cls is not None:
                terminate_plugin(md)
        return plugin_pb2.Empty()


def component_to_json_public(comp) -> dict:
    from astrbot._bridge.serialize import component_to_json

    return component_to_json(comp)


class FakeQuartRequest:
    """模拟 quart 全局 request（插件 handler 常 `from quart import request`）。

    对齐插件代码使用的接口：method/path/headers/args/form/files/get_json/
    json/max_content_length/cookies/query_string/url/endpoint/host/range/
    accept/content_type 等。
    """

    def __init__(self, plugin_req, path_params: dict):
        self._pr = plugin_req
        self.path_params = path_params
        self.method = plugin_req.method
        self.path = plugin_req.path
        # host / query_string / url 从请求头与 query 参数还原（不能硬编码占位
        # 值：插件做签名校验、OAuth 回调、webhook 验证时依赖真实值）。
        host = (
            plugin_req.headers.get("host")
            or plugin_req.headers.get("x-forwarded-host")
            or "localhost"
        )
        self.host = host
        pairs = plugin_req.query.multi_items()
        from urllib.parse import quote

        self.query_string = "&".join(
            f"{quote(str(k), safe='')}={quote(str(v), safe='')}" for k, v in pairs
        )
        self.url = f"http://{host}{plugin_req.path}" + (
            f"?{self.query_string}" if self.query_string else ""
        )
        self.full_path = self.url if self.query_string else plugin_req.path
        self.endpoint = plugin_req.path
        self.remote_addr = plugin_req.client_host or ""
        self.max_content_length = 64 * 1024 * 1024
        self.headers = FakeHeaders(plugin_req.headers)
        self.cookies = plugin_req.cookies
        self.content_type = plugin_req.content_type
        self.args = FakeMultiDict([(k, v) for k, v in plugin_req.query.multi_items()])
        self._files = plugin_req._files_cache
        self._form = plugin_req._form_cache
        self._body = plugin_req._raw_body
        # quart 的 request 代理是 _cv_request.get().request —— 需要提供
        # .request 属性（返回自身），否则 qreq.args 等访问到 None。
        self.request = self
        # quart 其他全局（session/g）由插件自持，不在本模拟范围。
        self.session = None
        self.g = None

    async def get_data(self, *a, **k):
        return self._body

    async def get_json(self, *a, **k):
        import json as _json

        try:
            return _json.loads(self._body.decode("utf-8"))
        except Exception:
            return None

    async def get_form(self, *a, **k):
        return await self._pr.form()

    async def form(self):
        return await self._pr.form()

    async def files(self):
        return await self._pr.files()

    @property
    def json(self):
        import json as _json

        try:
            return _json.loads(self._body.decode("utf-8"))
        except Exception:
            return None

    @property
    def query(self):
        return self.args

    def __getattr__(self, key):
        return getattr(self._pr, key, None)


class FakeHeaders:
    """大小写不敏感的 headers 容器（对齐 werkzeug/quart Headers 常用接口）。"""
    def __init__(self, data: dict[str, str]):
        self._data = {k.lower(): v for k, v in data.items()}

    def get(self, key, default=None):
        return self._data.get(key.lower(), default)

    def __getitem__(self, key):
        return self._data[key.lower()]

    def __contains__(self, key):
        return key.lower() in self._data

    def items(self):
        return list(self._data.items())

    def __iter__(self):
        return iter(self._data.items())

    def getlist(self, key, default=None):
        v = self._data.get(key.lower())
        return [v] if v is not None else (default if default is not None else [])


class FakeMultiDict:
    """类 werkzeug MultiDict（request.args.get(key) 等）。"""

    def __init__(self, pairs: list[tuple[str, str]]):
        self._pairs = pairs

    def get(self, key, default=None, type=None):
        # werkzeug MultiDict 语义：返回第一次出现的值（reversed 会取到
        # 最后一次，与 quart/werkzeug 行为相反）
        for k, v in self._pairs:
            if k != key:
                continue
            if type is not None:
                try:
                    return type(v)
                except (TypeError, ValueError):
                    return default
            return v
        return default

    def getlist(self, key):
        return [v for k, v in self._pairs if k == key]

    def __getitem__(self, key):
        v = self.get(key)
        if v is None:
            raise KeyError(key)
        return v

    def __contains__(self, key):
        return any(k == key for k, _ in self._pairs)

    def items(self):
        return list(self._pairs)

    def multi_items(self):
        return list(self._pairs)

    def keys(self):
        return list(dict.fromkeys(k for k, _ in self._pairs))


class FakeJSONProvider:
    """模拟 quart JSONProvider：jsonify() 的 response 构造。"""

    def __init__(self):
        self.dumps = json.dumps
        self.loads = json.loads

    def response(self, *args, **kwargs):
        from quart.wrappers.response import Response

        if len(args) == 1 and not kwargs and not isinstance(args[0], (tuple, list)):
            data = args[0]
        elif len(args) == 1 and isinstance(args[0], (tuple, list)) and len(args[0]) > 1 and isinstance(args[0][1], int):
            # (dict, status) 元组
            data = args[0][0]
            status = args[0][1]
            body = json.dumps(data, ensure_ascii=False).encode()
            return Response(body, status=status, headers={"Content-Type": "application/json"})
        else:
            data = list(args) if args else kwargs
        body = json.dumps(data, ensure_ascii=False).encode()
        return Response(body, status=200, headers={"Content-Type": "application/json"})


class FakeQuartApp:
    """模拟 quart app（current_app），支撑 jsonify / make_response / send_file /
    url_for 等在插件 Web handler 中的使用。"""

    def __init__(self):
        self.config: dict = {}
        self.url_map: dict = {}
        self.json = FakeJSONProvider()
        self.jinja_env = None

    def url_for(self, endpoint: str, **values):
        path = "/" + endpoint.lstrip("/")
        if values:
            path += "?" + "&".join(f"{k}={v}" for k, v in values.items())
        return path

    async def send_file(self, path, *args, **kwargs):
        from quart.wrappers.response import Response

        with open(path, "rb") as f:
            content = f.read()
        headers = {"Content-Type": "application/octet-stream"}
        filename = str(path).split("/")[-1]
        if "." in filename:
            ext = filename.rsplit(".", 1)[-1].lower()
            import mimetypes

            ct = mimetypes.guess_type(filename)[0]
            if ct:
                headers["Content-Type"] = ct
        return Response(content, status=200, headers=headers)

    async def make_response(self, result, *args, **kwargs):
        from quart.wrappers.response import Response

        if isinstance(result, Response):
            return result
        if isinstance(result, tuple) and len(result) >= 2 and isinstance(result[1], int):
            value, status = result[0], result[1]
            extra_headers: dict = {}
            if len(result) >= 3:
                raw_headers = result[2]
                if isinstance(raw_headers, dict):
                    extra_headers = dict(raw_headers)
                elif isinstance(raw_headers, (list, tuple)):
                    extra_headers = dict(raw_headers)
            if isinstance(value, dict):
                body = json.dumps(value, ensure_ascii=False).encode()
                headers_out = {"Content-Type": "application/json"}
                headers_out.update(extra_headers)
                return Response(body, status=status, headers=headers_out)
            return Response(
                str(value).encode(),
                status=status,
                headers=extra_headers or None,
            )
        if isinstance(result, dict):
            body = json.dumps(result, ensure_ascii=False).encode()
            return Response(body, status=200, headers={"Content-Type": "application/json"})
        if isinstance(result, str):
            return Response(result.encode(), status=200)
        return Response(b"", status=200)


class FakeQuartAppCtx:
    """quart AppContext 形状：_cv_app.get().app 取到 FakeQuartApp。"""

    def __init__(self):
        self.app = FakeQuartApp()
