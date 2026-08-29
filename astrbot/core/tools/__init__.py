"""内置工具包（Go 宿主兼容运行时，对齐本体 astrbot.core.tools）。

宿主 Go agent 循环原生装配/执行以下内置工具（computer/cron/knowledge_
base/message/web_search），SDK 侧提供同名薄壳类保证插件 import 面完整；
工具执行在宿主侧，SDK 薄壳不直接调用宿主。
"""
from __future__ import annotations

from astrbot.core.tools.registry import (  # noqa: F401
    builtin_tool,
    ensure_builtin_tools_loaded,
    get_builtin_tool_class,
    get_builtin_tool_config_rule,
    get_builtin_tool_config_statuses,
    get_builtin_tool_config_tags,
    get_builtin_tool_name,
    iter_builtin_tool_classes,
)

__all__ = [
    "builtin_tool",
    "ensure_builtin_tools_loaded",
    "get_builtin_tool_class",
    "get_builtin_tool_config_rule",
    "get_builtin_tool_config_statuses",
    "get_builtin_tool_config_tags",
    "get_builtin_tool_name",
    "iter_builtin_tool_classes",
]