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
from astrbot._runtime.registry import PluginRegistry, PluginSession
from astrbot._runtime.states import PluginHealthState, PluginLifecycleState

if TYPE_CHECKING:
    from astrbot._bridge.dispatch import PluginServiceServicer

logger = logging.getLogger("astrbot.runtime.host")


def _plugin_id_from_request(request) -> str:
    """从请求取 plugin_id；无该字段（旧 SDK / 单插件进程）返回空串。"""
    return getattr(request, "plugin_id", "") or ""


class MultiTenantPluginService(plugin_pb2_grpc.PluginServiceServicer):
    """统一 PluginService 入口：按 plugin_id 路由到对应插件的 servicer。

    单插件进程（python-grpc，无 plugin_id）下退化为委托到唯一插件——保持
    与直接使用 PluginServiceServicer 等价。
    """

    def __init__(self, registry: PluginRegistry, servicers: dict[str, "PluginServiceServicer"]) -> None:
        self._registry = registry
        self._servicers = servicers

    # ---- 路由核心 ----
    def _route(self, plugin_id: str):
        """返回 (session, servicer)；找不到时抛 KeyError。"""
        session = self._registry.get(plugin_id)
        if session is None:
            raise KeyError(f"plugin_id {plugin_id!r} 不在共享 Runtime 中")
        servicer = self._servicers.get(plugin_id)
        if servicer is None:
            raise KeyError(f"plugin_id {plugin_id!r} 没有对应 servicer")
        return session, servicer

    def _dispatch(self, plugin_id: str, method: str, *args, **kwargs):
        """设 ContextVar → 委托 → reset（异常也 reset）。"""
        session, servicer = self._route(plugin_id)
        token = set_current_session(session)
        try:
            return getattr(servicer, method)(*args, **kwargs)
        finally:
            reset_current_session(token)

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
            return self._dispatch(pid, "Register", request, context)
        return self._dispatch_or_single(request, "Register", context)

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
        """Runtime 级健康检查（不按插件路由）。"""
        from astrbot._bridge.gen import plugin_pb2

        return plugin_pb2.HealthResponse(ok=True, load=0.0, version="")


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

        self.multi = MultiTenantPluginService(self.registry, self._servicers)
        plugin_pb2_grpc.add_PluginServiceServicer_to_server(self.multi, server)
        return self.multi

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
