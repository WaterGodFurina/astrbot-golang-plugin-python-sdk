"""Python 桥启动的可复用阶段（单插件 server.py 与共享 Runtime 共用）。

把原先内联在 ``astrbot._bridge.server.main`` 里的启动步骤抽成独立函数，供
两条入口复用（方案第 9 节「server.py 单插件启动流程改为可被 Runtime 复用的
host 初始化；保留旧入口」）：

- :func:`setup_logging` 日志初始化；
- :func:`check_dependencies` 握手/版本/venv 前置校验；
- :func:`start_event_loop` 启动插件侧事件循环；
- :func:`install_bridge` 创建并预连接 HostBridge（宿主反向调用通道）；
- :func:`start_grpc_server` 启动 gRPC server（PluginService + GRPCBroker +
  GRPCStdio）并打印 go-plugin 握手行；
- :func:`install_signal_handlers` SIGTERM/SIGINT → SystemExit（走正常清理）。

单插件入口在 ``register`` 回调里注册唯一的 ``PluginServiceServicer``；共享
Runtime 入口注册 ``MultiTenantPluginService``。两者不重写 Bridge/协议。
"""

from __future__ import annotations

import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

logger = logging.getLogger("astrbot")


def setup_logging() -> None:
    import os

    level = os.environ.get("ASTRBOT_PLUGIN_LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))


def check_dependencies(plugin_dirname: str) -> None:
    """握手/版本/venv 前置校验；失败抛异常（调用方按 phase 输出 STARTUP_ERROR）。"""
    import os

    from astrbot._bridge import go_handshake

    go_handshake.check_magic_cookie()
    go_handshake.check_no_multiplex()
    import grpc  # noqa: F401

    if sys.version_info < (3, 10):
        raise RuntimeError(
            f"需要 Python >= 3.10，当前 {sys.version_info.major}.{sys.version_info.minor}"
        )
    in_venv = getattr(sys, "base_prefix", sys.prefix) != sys.prefix
    if os.environ.get("ASTRBOT_PLUGIN_DATA_DIR"):
        logger.info(
            f"环境检查通过: python={sys.version.split()[0]} venv={in_venv} "
            f"data_dir={os.environ['ASTRBOT_PLUGIN_DATA_DIR']}"
        )
    else:
        logger.warning("ASTRBOT_PLUGIN_DATA_DIR 未设置（非宿主启动？）")


def start_event_loop() -> None:
    from astrbot._bridge import loop as event_loop

    event_loop.start()


def install_bridge():
    """创建 HostBridge、后台预连接并返回；是否注册模块级单例由调用方决定。

    共享 Runtime 下 HostBridge 是 Runtime 级共享对象；插件身份由调用链显式
    传参 / ContextVar 决定（见 host.current_plugin_identity）。
    """
    from astrbot._bridge.host import HostBridge

    bridge = HostBridge()
    threading.Thread(target=bridge.preconnect, daemon=True).start()
    return bridge


def start_grpc_server(register: Callable) -> tuple:
    """启动 gRPC server：register(server) 注册 PluginService（含 broker/stdio）。

    返回 ``(server, bound_port)``；握手行已打印（go-plugin 宿主据此连接）。
    gRPC server 必须先于插件加载启动：宿主 Dispense 时 Accept(broker 9000)
    推送 ConnInfo，插件 __init__ 同步调宿主 GetConfig 依赖该 ConnInfo。
    """
    from astrbot._bridge import go_handshake
    from astrbot._bridge.broker import get_broker
    from astrbot._bridge.gen import goplugin_pb2_grpc
    from astrbot._bridge.stdio import register_grpc_stdio

    server = _new_grpc_server()
    register(server)
    goplugin_pb2_grpc.add_GRPCBrokerServicer_to_server(get_broker(), server)
    register_grpc_stdio(server)

    bound_port = _bind_server_port(server, go_handshake)
    server.start()
    go_handshake.print_handshake_line("127.0.0.1", bound_port)
    logger.info("插件桥接服务已启动，等待宿主连接")
    return server, bound_port


def _new_grpc_server():
    import grpc

    return grpc.server(
        ThreadPoolExecutor(max_workers=16),
        options=[
            ("grpc.max_send_message_length", 128 * 1024 * 1024),
            ("grpc.max_receive_message_length", 128 * 1024 * 1024),
        ],
    )


def _bind_server_port(server, go_handshake) -> int:
    min_port, max_port = go_handshake.port_range()
    if min_port and max_port:
        for port in range(min_port, max_port + 1):
            try:
                bound = server.add_insecure_port(f"127.0.0.1:{port}")
            except Exception:
                continue
            if bound:  # grpc-python 绑定失败不抛异常，返回 0
                return bound
        raise RuntimeError(
            f"无法在 PLUGIN_MIN_PORT={min_port}..PLUGIN_MAX_PORT={max_port} 范围内绑定端口"
        )
    return server.add_insecure_port("127.0.0.1:0")


def install_signal_handlers() -> None:
    """SIGTERM/SIGINT → SystemExit(0)，走正常清理路径（stop server / loop）。"""
    import signal

    def _handle(signum, frame):
        logger.info(f"收到信号 {signum}，进入清理流程")
        raise SystemExit(0)

    try:
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, _handle)
            signal.signal(signal.SIGINT, _handle)
    except (ImportError, ValueError, OSError):
        pass
