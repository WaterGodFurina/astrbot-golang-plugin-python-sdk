"""PluginService 实现：宿主 RPC → Python 插件 handler。"""
from __future__ import annotations

import inspect
import json
import logging

from astrbot._bridge import loop
from astrbot._bridge.gen import plugin_pb2, plugin_pb2_grpc
from astrbot._bridge.serialize import result_to_json
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
from astrbot.core.star.star_handler import EventType, star_handlers_registry

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
        except Exception:
            pass
        if first in ("self", "cls") or first is None:
            bound = getattr(inst, handler.__name__, None)
            if bound is not None and inspect.ismethod(bound):
                return bound
    return handler


def _fit_hook_args(handler, event=None, payload=None):
    """按 handler 声明的参数个数截断传参（对齐 Python 本体语义：
    on_astrbot_loaded/on_platform_loaded 无参，on_plugin_loaded 传 metadata，
    on_llm_response 传 event+response 等）。多余参数不传，避免
    "takes N positional arguments but M were given"。"""
    try:
        sig = inspect.signature(handler)
        n = sum(
            1
            for p in sig.parameters.values()
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        )
    except (ValueError, TypeError):
        n = 1
    args = []
    if n >= 1 and event is not None:
        args.append(event)
    if n >= 2 and payload is not None:
        args.append(payload)
    return args


class PluginServiceServicer(plugin_pb2_grpc.PluginServiceServicer):
    def __init__(self, plugin_name: str, plugin_version: str, plugin_desc: str, plugin_author: str, plugin_dir: str = ""):
        self.plugin_name = plugin_name
        self.plugin_version = plugin_version
        self.plugin_desc = plugin_desc
        self.plugin_author = plugin_author
        self.plugin_dir = plugin_dir
        # plugin_id -> Star 实例（loader 填充）
        self.inst: object | None = None
        # 命令注册：完整命令名/别名 -> (CommandFilter, handler)
        self.commands: dict[str, tuple[CommandFilter, object]] = {}
        self.filter_handlers: list[tuple[str, object, object]] = []  # (name, handler, inst)
        self.hook_handlers: dict[str, tuple[str, object, object]] = {}  # name -> (event, handler, inst)
        self.tools: dict[str, tuple[str, object]] = {}  # tool_name -> (func, inst)

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

        for md in star_handlers_registry.all():
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
        self.build_registry()
        resp = plugin_pb2.RegisterResponse(
            name=self.plugin_name,
            version=self.plugin_version,
            description=self.plugin_desc,
            author=self.plugin_author,
            config_schema_json=json.dumps(self._load_config_schema()).encode(),
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
            resp.commands.append(
                plugin_pb2.CommandDesc(
                    name=cmd_name,
                    aliases=list(f.alias) if f.alias else [],
                    description=desc,
                    permission=perm,
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
        return resp

    def _find_command(self, name: str):
        for full_name, (f, handler) in self.commands.items():
            if full_name == name or f.command_name == name:
                return f, handler
        return None, None

    def HandleCommand(self, request, context) -> plugin_pb2.HandleCommandResponse:
        f, handler = self._find_command(request.name)
        resp = plugin_pb2.HandleCommandResponse()
        if f is None or handler is None:
            return resp
        event_data = json.loads(request.event_json) if request.event_json else {}
        event = AstrMessageEvent.from_event_json(event_data)
        event.is_at_or_wake_command = True

        # 参数转换（对齐 Python validate_and_convert_params；宿主已传拆分后的 args）
        params = {}
        try:
            if request.args:
                params = f.validate_and_convert_params(list(request.args), f.handler_params)
            else:
                params = {}
        except ValueError as e:
            resp.text = f"参数错误: {e}"
            return resp

        bound = _bind(handler, self.inst)
        try:
            results = _call(bound, event, **params)
        except Exception as e:
            logger.error(f"命令 {request.name} 执行失败: {e}")
            resp.text = f"插件执行失败: {e}"
            return resp

        chain: list[dict] = []
        stop = False
        for r in results:
            if r is None:
                continue
            if isinstance(r, ProviderRequest):
                # request_llm：不设 Result，宿主继续默认 LLM
                continue
            if isinstance(r, dict):
                chain.append(r)
                continue
            c, s = result_to_json(r)
            chain.extend(c)
            stop = stop or s
        if chain:
            resp.chain_json = json.dumps(chain).encode()
        resp.stop = stop
        return resp

    def HandleFilter(self, request, context) -> plugin_pb2.HandleFilterResponse:
        event_data = json.loads(request.event_json) if request.event_json else {}
        event = AstrMessageEvent.from_event_json(event_data)
        for name, handler, inst in self.filter_handlers:
            if name == request.name:
                # 先跑注册的过滤器（regex / 事件类型 / 平台 / 权限 / 自定义），
                # 全部匹配才调用 handler；不匹配直接放行（不调用、不拦截）。
                from astrbot.core.star.filter.command import CommandFilter
                from astrbot.core.star.star_handler import star_handlers_registry

                md = star_handlers_registry.get_handler_by_full_name(request.name)
                if md is not None:
                    cfg = None
                    try:
                        cfg = getattr(inst, "config", None)
                    except Exception:
                        pass
                    for f in md.event_filters:
                        if isinstance(f, CommandFilter):
                            continue
                        try:
                            if not f.filter(event, cfg):
                                return plugin_pb2.HandleFilterResponse(allow=True)
                        except Exception:
                            return plugin_pb2.HandleFilterResponse(allow=True)
                bound = _bind(handler, inst)
                try:
                    results = _call(bound, event)
                except Exception as e:
                    logger.error(f"过滤器 {request.name} 执行失败: {e}")
                    return plugin_pb2.HandleFilterResponse(allow=True)
                allow = True
                for r in results:
                    if r is False:
                        allow = False
                    elif r is True:
                        allow = True
                return plugin_pb2.HandleFilterResponse(allow=allow)
        return plugin_pb2.HandleFilterResponse(allow=True)

    def HandleHook(self, request, context) -> plugin_pb2.HookResponse:
        resp = plugin_pb2.HookResponse(handled=False)
        entry = self.hook_handlers.get(request.name)
        if entry is None:
            return resp
        event_name, handler, inst = entry
        event_data = json.loads(request.event_json) if request.event_json else {}
        event = AstrMessageEvent.from_event_json(event_data)

        if event_name in ("on_decorating_result", "on_result_handling"):
            chain = []
            if request.chain_json:
                try:
                    chain = json.loads(request.chain_json)
                except Exception:
                    chain = []
            from astrbot._bridge.serialize import component_from_json

            comps = [component_from_json(c) for c in chain]
            result = MessageEventResult(comps)
            bound = _bind(handler, inst)
            try:
                results = _call(bound, event, result)
            except Exception as e:
                logger.error(f"结果钩子 {request.name} 执行失败: {e}")
                return resp
            new_result = None
            for r in results:
                if r is not None:
                    new_result = r
            if new_result is not None:
                if isinstance(new_result, MessageEventResult):
                    comps = new_result.chain or []
                elif isinstance(new_result, str):
                    comps = [result.chain[0]] if result.chain else []
                resp.chain_json = json.dumps(
                    [component_to_json_public(c) for c in comps]
                ).encode()
                resp.stop = bool(
                    new_result.result_type == EventResultType.STOP
                ) if isinstance(new_result, MessageEventResult) else False
                resp.handled = True
            return resp

        payload = None
        if event_name == "on_llm_response":
            pl = LLMResponse()
            if request.payload_json:
                try:
                    data = json.loads(request.payload_json)
                    pl._completion_text = data.get("text", "")
                except Exception:
                    pass
            payload = pl
        elif event_name in ("on_using_llm_tool", "on_llm_tool_respond"):
            payload = ToolCall()
            if request.payload_json:
                try:
                    data = json.loads(request.payload_json)
                    payload.tool_name = data.get("tool_name", "")
                    payload.tool_args = data.get("tool_args") or {}
                except Exception:
                    pass
        elif event_name == "on_plugin_error":
            payload = PluginError()
            if request.payload_json:
                try:
                    data = json.loads(request.payload_json)
                    payload.handler_name = data.get("handler_name", "")
                    payload.error = data.get("error", "")
                except Exception:
                    pass

        bound = _bind(handler, inst)
        try:
            results = _call(bound, *_fit_hook_args(bound, event, payload))
        except Exception as e:
            logger.error(f"钩子 {request.name} ({event_name}) 执行失败: {e}")
            return resp
        for r in results:
            if isinstance(r, MessageEventResult) and r.is_stopped():
                resp.stop = True
        resp.handled = True
        return resp

    def HandleLLMRequest(self, request, context) -> plugin_pb2.HandleLLMRequestResponse:
        resp = plugin_pb2.HandleLLMRequestResponse(system_prompt=request.system_prompt)
        entry = self.hook_handlers.get(request.name)
        if entry is None:
            return resp
        _, handler, inst = entry
        event_data = json.loads(request.event_json) if request.event_json else {}
        event = AstrMessageEvent.from_event_json(event_data)
        req = ProviderRequest(
            prompt=request.user_prompt,
            system_prompt=request.system_prompt,
        )
        bound = _bind(handler, inst)
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
        resp.system_prompt = req.system_prompt or ""
        resp.stop = bool(getattr(req, "stop", False))
        return resp

    def HandleTool(self, request, context) -> plugin_pb2.HandleToolResponse:
        entry = self.tools.get(request.name)
        if entry is None:
            return plugin_pb2.HandleToolResponse(text=f"工具 {request.name} 未找到", is_error=True)
        tool, inst = entry
        event_data = json.loads(request.event_json) if request.event_json else {}
        event = AstrMessageEvent.from_event_json(event_data)
        args = {}
        if request.args_json:
            try:
                args = json.loads(request.args_json)
            except Exception:
                args = {}
            if not isinstance(args, dict):
                args = {}
        handler = tool.handler
        bound = _bind(handler, inst)
        try:
            results = _call(bound, event, **args)
        except Exception as e:
            logger.error(f"工具 {request.name} 执行失败: {e}")
            return plugin_pb2.HandleToolResponse(
                text=f"工具 {request.name} 执行失败: {e}", is_error=True
            )
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
        return plugin_pb2.HandleToolResponse(text="\n".join(t for t in texts if t))

    def HealthCheck(self, request, context) -> plugin_pb2.HealthResponse:
        return plugin_pb2.HealthResponse(ok=True, version=self.plugin_version)

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
