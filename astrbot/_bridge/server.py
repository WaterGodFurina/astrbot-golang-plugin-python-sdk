"""Python 插件 gRPC 入口。

用法（宿主启动）：
    python3 -m astrbot._bridge.server <plugin_dir>
环境：
    PYTHONPATH 须包含本 SDK 包根目录（astrbot/ 的父目录）
    ASTRBOT_PLUGIN_DATA_DIR = 插件数据目录（cwd 通常已是）

启动链路按 phase 输出进度（stderr，见 progress.py）：
    dependency_check → bridge_init → grpc_start → plugin_import →
    registry_build → instantiate → running
任一阶段失败打一行 STARTUP_ERROR（phase/type/plugin/error 字段）后退出 1。
"""
from __future__ import annotations

import logging
import os
import sys
import time

from astrbot._bridge import bootstrap
from astrbot._bridge.deps_resolver import install_import_resolver

# 懒加载依赖解析器必须在一切重依赖 import（grpc 等）之前挂上：
# meta_path 全局生效后，插件代码 import 缺失的第三方依赖时按 IMPORT_MAP
# 自动 pip 安装（分层安装：宿主只预装核心层 grpcio/protobuf，其余见
# deps_resolver.py）。挂在 sys.meta_path 末尾，已装包零开销。
install_import_resolver()

HOST_SERVICE_APP_ID = 9000

logger = logging.getLogger("astrbot")


def _setup_logging() -> None:
    bootstrap.setup_logging()


def _startup_failed(phase: str, exc: BaseException, plugin_dirname: str) -> int:
    """按 phase 协议输出一行 STARTUP_ERROR 并打印诊断，随后退出 1。"""
    from astrbot._bridge import progress

    progress.emit_startup_error(phase, exc, plugin_dirname)
    if exc.__traceback__ is not None:
        import traceback

        traceback.print_exception(type(exc), exc, exc.__traceback__)
    return 1


def main() -> int:
    _setup_logging()
    if len(sys.argv) < 2:
        print("usage: python3 -m astrbot._bridge.server <plugin_dir>", file=sys.stderr)
        return 2
    plugin_dir = os.path.abspath(sys.argv[1])
    plugin_dirname = os.path.basename(plugin_dir)

    from astrbot._bridge import progress
    from astrbot._bridge.state import LifecycleStateMachine

    lifecycle = LifecycleStateMachine()

    # 1) phase=dependency_check：go-plugin 握手校验、Python 版本、grpc、
    #    venv 标记。握手行打印前不得碰 stdout（进度/错误全走 stderr）。
    try:
        progress.emit_phase("dependency_check")
        bootstrap.check_dependencies(plugin_dirname)
    except Exception as e:
        return _startup_failed("dependency_check", e, plugin_dirname)

    # 2) phase=bridge_init：事件循环 + 宿主桥（HostService 反向调用）：先创建
    #    并预连接（身份先用目录名占位，load_plugin 后更新为注册名）。插件
    #    __init__/get_config() 会同步调宿主（GetConfig）。
    try:
        progress.emit_phase("bridge_init")
        bootstrap.start_event_loop()

        from astrbot._bridge.host import set_bridge
        from astrbot.core.star.context import set_host_bridge

        bridge = bootstrap.install_bridge()
        bridge.plugin_name = plugin_dirname
        bridge.plugin_id = ""
        set_host_bridge(bridge)
        lifecycle.set(LifecycleStateMachine.BRIDGE_READY)
    except Exception as e:
        return _startup_failed("bridge_init", e, plugin_dirname)

    # 3) phase=grpc_start：gRPC server（PluginService + GRPCBroker）必须先于
    #    插件加载启动：宿主在 Dispense 时 Accept(9000) 会连插件的 GRPCBroker
    #    推送 ConnInfo；插件 __init__ 里同步调 get_config（HostService）依赖
    #    这个 ConnInfo。server 未就绪 → dial 等 ConnInfo 超时 → 插件卡死。
    try:
        progress.emit_phase("grpc_start")
        from astrbot._bridge.dispatch import PluginServiceServicer
        from astrbot._bridge.gen import plugin_pb2_grpc

        servicer = PluginServiceServicer(plugin_dirname, "", "", "", plugin_dir)
        # 复用 servicer 的状态机（避免 server 侧与 dispatch 侧两套互不相通的
        # 状态机）；先把当前状态（BRIDGE_READY）平移过去，后续推进统一生效。
        servicer.lifecycle.set(lifecycle.state())
        lifecycle = servicer.lifecycle

        def _register(server):
            plugin_pb2_grpc.add_PluginServiceServicer_to_server(servicer, server)

        server, _bound_port = bootstrap.start_grpc_server(_register)
        lifecycle.set(LifecycleStateMachine.GRPC_READY)
        lifecycle.set(LifecycleStateMachine.HANDSHAKE_SENT)
    except Exception as e:
        return _startup_failed("grpc_start", e, plugin_dirname)

    # 4) phase=plugin_import：插件加载阶段 A（import 模块，填充 star 注册表；
    #    不实例化）。
    try:
        progress.emit_phase("plugin_import")
        from astrbot._bridge.loader import load_plugin_import
        from astrbot.core.star.context import Context

        context = Context()
        metadata = load_plugin_import(plugin_dir, context)
        if metadata is None:
            raise RuntimeError("插件加载失败：未找到 Star 类")
        logger.info(
            f"诊断: plugin_dir={plugin_dir} module={getattr(metadata.module, '__name__', '?')} "
            f"name={metadata.name} star={metadata.star_cls_type}"
        )

        plugin_name = metadata.name or plugin_dirname
        plugin_version = metadata.version or ""
        plugin_desc = metadata.desc or ""
        plugin_author = metadata.author or ""
    except Exception as e:
        return _startup_failed("plugin_import", e, plugin_dirname)

    # 5) phase=registry_build：注册表元数据注入 + 放行宿主 Register + 等
    #    Register 完成（宿主完成 HostService 身份绑定后置 REGISTERED）。
    try:
        progress.emit_phase("registry_build")
        servicer.plugin_name = plugin_name
        servicer.plugin_version = plugin_version
        servicer.plugin_desc = plugin_desc
        servicer.plugin_author = plugin_author
        servicer.web_apis = list(getattr(context, "_web_apis", []))
        # Register 快照仍随 Register RPC 上报（兼容旧宿主）；运行期新增的
        # 路由经 ListWebApis RPC 实时拉取，宿主网关无需重启即可转发。
        context.plugin_name = plugin_name
        context.plugin_id = metadata.plugin_id
        try:
            setattr(metadata.star_cls_type, "plugin_id", metadata.plugin_id)
        except Exception:
            pass
        # 插件加载完成后用注册名更新宿主桥身份（GetConfig 等 RPC 参数用）。
        bridge.plugin_name = plugin_name
        bridge.plugin_id = metadata.plugin_id
        # 注册模块级单例：botpy/telegram 兼容层经 get_bridge() 取同一实例，
        # 否则 plugin_name 恒空导致桥接钩子注册失败。
        set_bridge(bridge)

        # 放行 Register（可能已等在 REGISTERING 上）；宿主 Register 完成后会
        # 绑定 HostService 身份，状态推进到 REGISTERED。
        servicer.mark_ready()
        logger.info(f"插件 {plugin_name} v{plugin_version} 模块加载完成，等待宿主注册")
        if not servicer.wait_registered(timeout=60):
            print("等待宿主 Register 超时", file=sys.stderr)
            return _startup_failed(
                "registry_build", TimeoutError("等待宿主 Register 超时"), plugin_dirname
            )
    except Exception as e:
        return _startup_failed("registry_build", e, plugin_dirname)

    # 6) phase=instantiate：插件加载阶段 B（实例化 Star）。宿主 Register 完成
    #    （身份绑定）后 __init__/get_config 才能通过宿主 GetConfig 身份校验。
    try:
        progress.emit_phase("instantiate")
        lifecycle.set(LifecycleStateMachine.INSTANTIATING)
        from astrbot._bridge.loader import instantiate_plugin

        instantiate_plugin(metadata, context)
        servicer.inst = metadata.star_cls
        # 实例化完成：放行命令/过滤器/钩子等 RPC（可能已等在 RUNNING 上）。
        servicer.mark_instanced()
    except Exception as e:
        return _startup_failed("instantiate", e, plugin_dirname)

    # 7) phase=running：常驻。注册 SIGTERM/SIGINT 处理：宿主 go-plugin 停插件
    #    发 SIGTERM，默认行为直接终止进程 → finally 里的清理（server.stop /
    #    event_loop.stop / 状态机 STOPPED）不会执行，插件 terminate 钩子也不
    #    会被调用。转成 SystemExit 走正常清理路径。
    bootstrap.install_signal_handlers()

    try:
        progress.emit_phase("running")
        while True:
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        lifecycle.set(LifecycleStateMachine.STOPPING)
        # stop(5)：给 in-flight RPC 5 秒宽限期完成，避免无宽限（stop(0)）立即
        # 终止导致宿主侧 RPC 中断/超时；先停 RPC 再停事件循环，顺序合理。
        server.stop(5)
        from astrbot._bridge import loop as event_loop

        event_loop.stop()
        lifecycle.set(LifecycleStateMachine.STOPPED)
    return 0


if __name__ == "__main__":
    sys.exit(main())
