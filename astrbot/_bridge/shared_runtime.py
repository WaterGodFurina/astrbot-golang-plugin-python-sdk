"""共享 Python Runtime（python-shared）进程入口。

用法（宿主启动）：
    python3 -m astrbot._bridge.shared_runtime

环境：
    ASTRBOT_SHARED_PLUGINS = JSON 数组，元素形如
        {"plugin_id": "...", "plugin_name": "...", "plugin_dir": "...", "version": "..."}
    PYTHONPATH 含 SDK 包根目录；ASTRBOT_PLUGIN_DATA_DIR = Runtime 数据目录。

与单插件 ``server.py`` 的关系：
- 复用同一套启动阶段（``bootstrap``：日志/依赖校验/事件循环/HostBridge/
  gRPC server + broker + stdio + 握手行/信号处理），不新建第二套 Bridge。
- 区别只在 PluginService 注册的是 ``MultiTenantPluginService``（一个进程服务
  多个插件，按请求 plugin_id 路由），启动后逐个 load 初始插件。
- 后续成员增删由宿主经 ``ManagePlugin`` RPC 驱动（load/unload），无需重启
  整个 Runtime（方案第 8 节）。

隔离边界：本进程内多插件是**逻辑级隔离**（PluginSession + ContextVar）；
``sys.modules`` / C 扩展 / 解释器级崩溃无法隔离，需要进程边界者走
python-grpc / python-isolated（方案第 7 节）。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time

from astrbot._bridge import bootstrap
from astrbot._bridge.deps_resolver import install_import_resolver

# 懒加载依赖解析器须在重依赖 import 前挂上（与 server.py 一致）。
install_import_resolver()

logger = logging.getLogger("astrbot.shared_runtime")


def _load_plugins_from_env() -> list[dict]:
    raw = os.environ.get("ASTRBOT_SHARED_PLUGINS", "") or "[]"
    try:
        data = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        logger.error("解析 ASTRBOT_SHARED_PLUGINS 失败: %s", e)
        return []
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict) and d.get("plugin_id")]


def _startup_failed(phase: str, exc: BaseException) -> int:
    from astrbot._bridge import progress

    progress.emit_startup_error(phase, exc, "shared-runtime")
    if exc.__traceback__ is not None:
        import traceback

        traceback.print_exception(type(exc), exc, exc.__traceback__)
    return 1


def main() -> int:
    bootstrap.setup_logging()
    from astrbot._bridge import progress

    try:
        progress.emit_phase("dependency_check")
        bootstrap.check_dependencies("shared-runtime")
    except Exception as e:  # noqa: BLE001
        return _startup_failed("dependency_check", e)

    try:
        progress.emit_phase("bridge_init")
        bootstrap.start_event_loop()
        from astrbot._bridge.host import set_bridge
        from astrbot.core.star.context import set_host_bridge
        from astrbot._runtime.host import SharedRuntimeHost

        bridge = bootstrap.install_bridge()
        bridge.plugin_name = ""
        bridge.plugin_id = ""
        set_bridge(bridge)
        set_host_bridge(bridge)
        host = SharedRuntimeHost()
    except Exception as e:  # noqa: BLE001
        return _startup_failed("bridge_init", e)

    try:
        progress.emit_phase("grpc_start")

        def _register(server):
            host.bind_server(server)

        server, _bound_port = bootstrap.start_grpc_server(_register)
        logger.info("共享 Runtime gRPC 服务已启动，等待宿主连接")
    except Exception as e:  # noqa: BLE001
        return _startup_failed("grpc_start", e)

    # 初始成员：逐个 import（插件级失败不影响其它插件，方案第 7 节）。
    try:
        progress.emit_phase("plugin_import")
        for spec in _load_plugins_from_env():
            pid = str(spec.get("plugin_id", ""))
            try:
                host.add_plugin(
                    pid,
                    plugin_name=str(spec.get("plugin_name", "") or ""),
                    plugin_dir=str(spec.get("plugin_dir", "") or ""),
                    version=str(spec.get("version", "") or ""),
                )
                host.load_plugin(pid)
                logger.info("共享 Runtime 已加载插件 %s", pid)
            except Exception as e:  # noqa: BLE001
                logger.error("共享 Runtime 加载插件 %s 失败: %s", pid, e)
        progress.emit_phase("running")
    except Exception as e:  # noqa: BLE001
        return _startup_failed("plugin_import", e)

    # Runtime 心跳：后台线程周期刷新 Watchdog 心跳时间戳，供 Go 侧判断 Runtime
    # 是否卡死（连接存活但心跳停滞）。进程级崩溃检测仍由 Go 负责。
    import threading

    def _heartbeat_loop() -> None:
        while True:
            host.watchdog.heartbeat()
            time.sleep(30)

    threading.Thread(target=_heartbeat_loop, name="runtime-heartbeat", daemon=True).start()

    bootstrap.install_signal_handlers()
    try:
        while True:
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        server.stop(5)
        from astrbot._bridge import loop as event_loop

        event_loop.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
