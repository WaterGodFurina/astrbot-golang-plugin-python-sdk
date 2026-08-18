"""AstrBot 全局异常（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.exceptions`（/ `astrbot.core.utils.pip_installer`
中导出的 DependencyConflictError），插件常用的
`from astrbot.core.exceptions import ...` / `from astrbot.core import ...`
路径均指向这里。
"""


class ProviderNotFoundError(Exception):
    """当按 ID 找不到 Provider 时抛出。"""


class DependencyConflictError(Exception):
    """当插件依赖与当前环境已安装依赖冲突时抛出（pip 安装失败时包装）。"""
