"""共享 Runtime Host（python-shared）：单进程 gRPC server，按 plugin_id 多租户路由。

对应方案文档：共享 Python Runtime 承载多个插件，复用现有 PluginService /
HostService 协议（板块 3 已加 plugin_id / PluginRef），按插件选择运行方式，与
单插件进程（python-grpc）**共存**。

工作方式：
- 每个插件一个 ``PluginServiceServicer`` 实例（持有该插件的 commands/inst/tools
  等状态）；一个 ``MultiTenantPluginService`` 作为统一入口，按请求里的
  ``plugin_id`` 路由到对应 servicer。
- 路由前 ``set_current_session(session)``、路由后 ``reset``（板块 4 的
  ContextVar）：servicer 内部经 SessionScopedProxy 访问的 ``star_map`` /
  ``star_handlers_registry`` / ``llm_tools`` 因此自动落到该插件的独立实例。
- ``HealthCheck`` 是 Runtime 级，不按插件路由。

隔离边界：本层是「逻辑级插件隔离」（ContextVar + PluginSession）。sys.modules /
path_compat / C 扩展等解释器级状态无法在此隔离——需要进程级隔离的插件应使用
python-grpc / python-isolated（见方案文档第 8/17 节）。
"""

from __future__ import annotations

import contextvars
import logging
from typing import TYPE_CHECKING

from astrbot._bridge.gen import plugin_pb2_grpc  # noqa: E402 -- 运行时基类依赖
from astrbot._runtime.context import (
    reset_current_session,
    set_current_session,
)
from astrbot._runtime.dispatcher import Dispatcher, PluginDispatchError
from astrbot._runtime.registry import PluginRegistry, PluginSession
from astrbot._runtime.states import PluginHealthState, PluginLifecycleState
from astrbot._runtime.watchdog import PluginWatchdog

if TYPE_CHECKING:
    from astrbot._bridge.dispatch import PluginServiceServicer

logger = logging.getLogger("astrbot.runtime.host")


def _plugin_id_from_request(request) -> str:
    """从请求取 plugin_id；无该字段（旧 SDK / 单插件进程）返回空串。"""
    return getattr(request, "plugin_id", "") or ""


def _manage_ok():
    from astrbot._bridge.gen import plugin_pb2

    return plugin_pb2.ManagePluginResponse(ok=True)


def _manage_err(message: str):
    from astrbot._bridge.gen import plugin_pb2

    return plugin_pb2.ManagePluginResponse(ok=False, error=message)


class MultiTenantPluginService(plugin_pb2_grpc.PluginServiceServicer):
    """统一 PluginService 入口：按 plugin_id 路由到对应插件的 servicer。

    单插件进程（python-grpc，无 plugin_id）下退化为委托到唯一插件——保持
    与直接使用 PluginServiceServicer 等价。
    """

    def __init__(
        self,
        registry: PluginRegistry,
        servicers: dict[str, "PluginServiceServicer"],
        dispatcher: Dispatcher | None = None,
        on_registered=None,
        on_manage=None,
        heartbeat=None,
    ) -> None:
        self._registry = registry
        self._servicers = servicers
        self._dispatcher = dispatcher or Dispatcher(registry, servicers)
        # Register 完成后触发实例化（共享 Runtime 下 server.py 的 instantiate
        # 阶段由 host 回调驱动）。
        self._on_registered = on_registered
        # ManagePlugin(load/unload) 处理回调（共享 Runtime 成员管理）。
        self._on_manage = on_manage
        # Runtime 心跳读取器（返回 Unix 秒），供 HealthCheck 上报。
        self._heartbeat = heartbeat

    # ---- 路由核心 ----
    def _route(self, plugin_id: str):
        """返回 (session, servicer)；找不到时抛 KeyError。"""
        return self._dispatcher.route(plugin_id)

    def _dispatch(self, plugin_id: str, method: str, *args, **kwargs):
        """经 Dispatcher 设 ContextVar → 委托 → reset（异常也 reset + 记录）。"""
        try:
            return self._dispatcher.dispatch(plugin_id, method, *args, **kwargs)
        except PluginDispatchError as e:
            # 插件边界内异常：记录 plugin_id 后向上抛，由 gRPC 转为错误响应，
            # 不杀 Runtime 内其它插件（方案第 7 节）。
            raise

    def _dispatch_or_single(self, request, method, *args, **kwargs):
        """有 plugin_id 按多租户路由；空 plugin_id 退化为单插件（若仅一个）。"""
        pid = _plugin_id_from_request(request)
        if pid:
            return self._dispatch(pid, method, request, *args, **kwargs)
        # 单插件进程 / 兼容旧宿主：委托到唯一 servicer（保持原行为）。
        if len(self._servicers) == 1:
            return getattr(next(iter(self._servicers.values())), method)(
                request, *args, **kwargs
            )
        raise KeyError("请求未带 plugin_id 且共享 Runtime 中存在多个插件")

    # ---- RPC 实现 ----
    def Register(self, request, context):
        pid = _plugin_id_from_request(request)
        if pid:
            resp = self._dispatch(pid, "Register", request, context)
            # Register 内已 mark_registered；实例化在 Register 返回后进行
            #（对齐单插件 server.py：instantiate 依赖宿主已完成身份绑定）。
            if self._on_registered is not None:
                try:
                    self._on_registered(pid)
                except Exception as e:  # noqa: BLE001
                    logger.error("插件 %s Register 后实例化触发失败: %s", pid, e)
            return resp
        return self._dispatch_or_single(request, "Register", context)

    def ManagePlugin(self, request, context):
        """共享 Runtime 成员管理（load/unload）；单插件进程返回 UNIMPLEMENTED。"""
        import grpc as _grpc

        if self._on_manage is None:
            context.abort(
                _grpc.StatusCode.UNIMPLEMENTED,
                "ManagePlugin 仅在共享 Runtime（python-shared）支持",
            )
        action = getattr(request, "action", "") or ""
        pid = getattr(request, "plugin_id", "") or ""
        if not pid:
            return _manage_err("ManagePlugin 需要 plugin_id")
        try:
            self._on_manage(request)
            return _manage_ok()
        except Exception as e:  # noqa: BLE001 -- 插件级失败不影响其它插件
            logger.error("ManagePlugin(%s, %s) 失败: %s", action, pid, e)
            return _manage_err(str(e))

    def HandleCommand(self, request, context):
        return self._dispatch_or_single(request, "HandleCommand", context)

    def HandleFilter(self, request, context):
        return self._dispatch_or_single(request, "HandleFilter", context)

    def HandleHook(self, request, context):
        return self._dispatch_or_single(request, "HandleHook", context)

    def HandleLLMRequest(self, request, context):
        return self._dispatch_or_single(request, "HandleLLMRequest", context)

    def HandleTool(self, request, context):
        return self._dispatch_or_single(request, "HandleTool", context)

    def ListTools(self, request, context):
        return self._dispatch_or_single(request, "ListTools", context)

    def ListWebApis(self, request, context):
        return self._dispatch_or_single(request, "ListWebApis", context)

    def HandleWebRequest(self, request, context):
        return self._dispatch_or_single(request, "HandleWebRequest", context)

    def SetLogLevel(self, request, context):
        return self._dispatch_or_single(request, "SetLogLevel", context)

    def FeedSessionWait(self, request, context):
        return self._dispatch_or_single(request, "FeedSessionWait", context)

    def GetConfigSchema(self, request, context):
        return self._dispatch_or_single(request, "GetConfigSchema", context)

    def Cleanup(self, request, context):
        return self._dispatch_or_single(request, "Cleanup", context)

    def FeedCronJob(self, request, context):
        return self._dispatch_or_single(request, "FeedCronJob", context)

    def HealthCheck(self, request, context):
        """Runtime 级健康检查：汇总共享 Runtime 内全部插件的状态（板块 5）。"""
        from astrbot._bridge.gen import plugin_pb2

        statuses = []
        for s in self._registry.all():
            statuses.append(
                plugin_pb2.PluginStatus(
                    plugin_id=s.plugin_id,
                    plugin_name=s.plugin_name,
                    state=s.lifecycle.value,
                    health=s.health.value,
                    last_activity=float(s.last_activity),
                    error=s.error,
                    generation=int(s.generation),
                )
            )
        hb = 0.0
        if self._heartbeat is not None:
            hb = float(self._heartbeat())
        return plugin_pb2.HealthResponse(
            ok=True, load=0.0, version="", plugins=statuses, runtime_heartbeat=hb
        )


class SharedRuntimeHost:
    """python-shared 共享 Runtime：加载多插件、按 plugin_id 路由、状态维护。

    生命周期（对齐方案）：
        创建 Host → 为每个插件创建 PluginSession + PluginServiceServicer →
        注册到 gRPC server → 宿主逐个 Register → 实例化插件 → 进入运行。
    """

    def __init__(self) -> None:
        from astrbot._bridge.dispatch import PluginServiceServicer  # 延迟导入避免启动依赖

        self.registry = PluginRegistry()
        self._servicers: dict[str, PluginServiceServicer] = {}
        self._servicer_cls = PluginServiceServicer
        self.multi = None  # 绑定 gRPC server 时创建
        # 插件健康 Watchdog：插件级失败计数 + 达到阈值上报 Go（方案第 6/9 节）。
        self.watchdog = PluginWatchdog()
        self.dispatcher = Dispatcher(self.registry, self._servicers, self.watchdog)

    def add_plugin(
        self,
        plugin_id: str,
        plugin_name: str = "",
        plugin_dir: str = "",
        version: str = "",
    ) -> PluginSession:
        """登记一个插件：创建 PluginSession 与对应 servicer（尚未加载/实例化）。"""
        session = PluginSession(
            plugin_id=plugin_id,
            plugin_name=plugin_name,
            plugin_dir=plugin_dir,
            version=version,
        )
        servicer = self._servicer_cls(
            plugin_name, version, "", "", plugin_dir
        )
        self.registry.add(session)
        self._servicers[plugin_id] = servicer
        return session

    def remove_plugin(self, plugin_id: str) -> None:
        """卸载插件：从 registry 与 servicer 映射中移除（幂等）。"""
        self.registry.remove(plugin_id)
        self._servicers.pop(plugin_id, None)

    def bind_server(self, server) -> "MultiTenantPluginService":
        """把多租户入口注册到 gRPC server，返回入口实例。"""
        from astrbot._bridge.gen import plugin_pb2_grpc

        self.multi = MultiTenantPluginService(
            self.registry,
            self._servicers,
            dispatcher=self.dispatcher,
            on_registered=self.instantiate_async,
            on_manage=self.manage_plugin,
            heartbeat=self.watchdog.last_heartbeat,
        )
        plugin_pb2_grpc.add_PluginServiceServicer_to_server(self.multi, server)
        return self.multi

    def manage_plugin(self, request) -> None:
        """ManagePlugin(load/unload) 处理：加载/卸载共享 Runtime 内单个插件。

        load：add_plugin + load_plugin（import + mark_ready），随后宿主对该
        plugin_id 调 Register，Register 完成后由 on_registered 触发 instantiate。
        unload：unload_plugin（terminate + 清理 session），不影响其它插件。
        """
        action = (getattr(request, "action", "") or "").lower()
        plugin_id = getattr(request, "plugin_id", "") or ""
        if action == "load":
            if self.registry.get(plugin_id) is None:
                self.add_plugin(
                    plugin_id,
                    plugin_name=getattr(request, "plugin_name", "") or "",
                    plugin_dir=getattr(request, "plugin_dir", "") or "",
                    version=getattr(request, "version", "") or "",
                )
            self.load_plugin(plugin_id)
        elif action == "unload":
            self.unload_plugin(plugin_id)
        else:
            raise ValueError(f"未知的 ManagePlugin action: {action!r}")

    def instantiate_async(self, plugin_id: str) -> None:
        """Register 返回后异步实例化目标插件（不阻塞 Register 响应）。"""
        import threading

        session = self.registry.get(plugin_id)
        if session is None:
            return
        metadata = session.instance
        if metadata is None:
            logger.error("插件 %s 无 metadata，跳过实例化", plugin_id)
            return

        def _run() -> None:
            try:
                self.instantiate(plugin_id, metadata)
            except Exception as e:  # noqa: BLE001 -- 插件级失败不影响其它插件
                logger.error("插件 %s Register 后实例化失败: %s", plugin_id, e)

        threading.Thread(
            target=_run, name=f"instantiate-{plugin_id}", daemon=True
        ).start()

    def sessions(self) -> list[PluginSession]:
        return self.registry.all()

    def plugin_states(self) -> list[dict]:
        """汇总各插件的状态（板块 5 状态上报的数据源）。"""
        out = []
        for s in self.registry.all():
            out.append(
                {
                    "plugin_id": s.plugin_id,
                    "plugin_name": s.plugin_name,
                    "state": s.lifecycle.value,
                    "health": s.health.value,
                    "error": s.error,
                    "last_activity": s.last_activity,
                    "generation": s.generation,
                }
            )
        return out

    def load_plugin(self, plugin_id: str) -> None:
        """在共享 Runtime 中加载/激活一个插件（阶段 4-6 接线）。

        复现单插件 server.py 的 load 流程，但在当前 PluginSession 的
        ContextVar 下执行：loader 经 SessionScopedProxy 写入 session 的独立
        registry（板块 4）。流程：

            初始化 session 独立 registry
                ↓
            ContextVar(session) 下 load_plugin_import（import + 发现 Star）
                ↓
            填充 servicer 身份（plugin_name/version/...）+ mark_ready
                ↓
            （宿主随后 Register，wait_registered 放行）
                ↓
            instantiate_plugin（实例化 Star，__init__ 需宿主已 Register）

        失败置 session 为 ERROR（插件级失败，不影响 Runtime 内其他插件）。
        """
        from astrbot.core.star.context import Context
        from astrbot._bridge import loader

        session = self.registry.get(plugin_id)
        if session is None:
            raise KeyError(f"plugin_id {plugin_id!r} 未登记")
        servicer = self._servicers[plugin_id]

        session.init_scoped_registries()
        session.lifecycle = PluginLifecycleState.LOADING
        token = set_current_session(session)
        try:
            try:
                context = Context()
                metadata = loader.load_plugin_import(session.plugin_dir, context)
                if metadata is None:
                    raise RuntimeError("未找到 Star 类")
                session.module = metadata.module
                session.instance = metadata

                plugin_name = metadata.name or session.plugin_name
                version = metadata.version or session.version
                session.plugin_name = plugin_name
                session.version = version

                servicer.plugin_name = plugin_name
                servicer.plugin_version = version
                servicer.plugin_desc = metadata.desc or ""
                servicer.plugin_author = metadata.author or ""
                servicer.web_apis = list(getattr(context, "_web_apis", []))
                context.plugin_name = plugin_name
                context.plugin_id = session.plugin_id

                session.lifecycle = PluginLifecycleState.ACTIVE
                session.touch()
                # 放行宿主 Register（可能已等在 REGISTERING 上）。
                servicer.mark_ready()
                logger.info(
                    "共享 Runtime 加载插件 %s v%s 完成，等待宿主注册",
                    plugin_name, version,
                )
            except Exception as e:
                session.lifecycle = PluginLifecycleState.ERROR
                session.health = PluginHealthState.UNHEALTHY
                session.error = str(e)
                logger.error("共享 Runtime 加载插件 %s 失败: %v", plugin_id, e)
                raise
        finally:
            reset_current_session(token)

    def instantiate(self, plugin_id: str, metadata) -> None:
        """宿主 Register 完成后实例化插件（阶段 6 的独立步骤）。"""
        from astrbot.core.star.context import Context
        from astrbot._bridge import loader

        session = self.registry.get(plugin_id)
        if session is None:
            raise KeyError(f"plugin_id {plugin_id!r} 未登记")
        servicer = self._servicers[plugin_id]
        context = Context()
        context.plugin_name = session.plugin_name
        context.plugin_id = session.plugin_id
        token = set_current_session(session)
        try:
            loader.instantiate_plugin(metadata, context)
            servicer.inst = metadata.star_cls
            session.instance = metadata.star_cls
            servicer.mark_instanced()
            session.lifecycle = PluginLifecycleState.ACTIVE
            session.touch()
        except Exception as e:
            session.lifecycle = PluginLifecycleState.ERROR
            session.error = str(e)
            logger.error("共享 Runtime 实例化插件 %s 失败: %v", plugin_id, e)
            raise
        finally:
            reset_current_session(token)

    def unload_plugin(self, plugin_id: str) -> None:
        """卸载插件：调用 terminate + 清理 session（不影响 Runtime 内其他插件）。"""
        from astrbot._bridge import loader

        session = self.registry.get(plugin_id)
        if session is None:
            return
        token = set_current_session(session)
        try:
            # session.instance 是 loader 返回的 StarMetadata（star_cls 为实例）。
            metadata = session.instance
            if metadata is not None and getattr(metadata, "star_cls", None) is not None:
                loader.terminate_plugin(metadata)
            session.lifecycle = PluginLifecycleState.UNLOADED
            session.tasks.cancel_all()
            self.registry.remove(plugin_id)
            self._servicers.pop(plugin_id, None)
        finally:
            reset_current_session(token)
