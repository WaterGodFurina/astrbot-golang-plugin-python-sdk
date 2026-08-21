"""AstrBot Go 宿主兼容运行时（Python SDK）。

插件在 Go 宿主（Astrbot-golang）中以 gRPC 子进程方式运行时 import 本包；
在 Python AstrBot 本体中 import 真正的 astrbot 包。两套运行时间名同构，
插件代码无需任何修改。
"""
import logging

# 版本号用 `+compat`（build metadata）而非 `-compat`（pre-release）：
# `-compat` 是 semver pre-release，语义上低于 4.27.3 正式版，插件做
# `>= "4.27.3"` 版本检查会误判为不满足；`+compat` 是 build metadata，
# 不参与版本号比较。
__version__ = "4.27.3+compat"

logger = logging.getLogger("astrbot")

# 仅当 root logger 尚无 handler 时才把 root logger 级别设为 INFO；不在此
# addHandler——import 期抢注 root logger 配置会破坏宿主/其他库的日志布局。
# 真正的日志 handler 由 server._setup_logging() 统一配置（含格式化与日志级别
# 环境变量）。此处 setLevel 仅为保证插件在宿主配置就绪前也能直接使用
# logging/astrbot.api.logger 而不产生空输出。
if not logging.getLogger().handlers:
    logging.getLogger().setLevel(logging.INFO)
