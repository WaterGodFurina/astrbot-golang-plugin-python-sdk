"""内置工具注册表（Go 宿主兼容运行时，对齐本体 tools/registry）。

宿主 Go agent 循环原生装配/执行内置工具（computer/cron/kb/message/
web_search），SDK 侧提供同名注册表接口（builtin_tool 装饰器等），供插件
import 与在不依赖宿主执行时使用；宿主执行在宿主侧，不经过本注册表。
"""
from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("astrbot")

_builtin_tool_classes_by_name: dict[str, type] = {}
"""名称 → 内置工具类（SDK 本地注册表，仅记录类，不执行）。"""

_builtin_tool_names_by_class: dict[type, str] = {}


def _resolve_builtin_tool_name(tool_cls: type) -> str:
    """解析内置工具名：优先类属性 name。"""
    name = getattr(tool_cls, "name", None)
    if isinstance(name, str) and name:
        return name
    fields = getattr(tool_cls, "__dataclass_fields__", {})
    field_def = fields.get("name")
    if field_def is not None and isinstance(getattr(field_def, "default", None), str):
        return field_def.default
    raise ValueError(
        f"Builtin tool class {tool_cls.__module__}.{tool_cls.__name__} does not define a valid name."
    )


def builtin_tool(tool_cls=None, *, config: dict | None = None):
    """内置工具注册装饰器（SDK 本地记录；宿主 Go 循环独立实现同名工具）。"""

    def _register(cls):
        tool_name = _resolve_builtin_tool_name(cls)
        existing = _builtin_tool_classes_by_name.get(tool_name)
        if existing is not None and existing is not cls:
            raise ValueError(
                f"Builtin tool name conflict detected: {tool_name} is already registered by "
                f"{existing.__module__}.{existing.__name__}.",
            )
        _builtin_tool_classes_by_name[tool_name] = cls
        _builtin_tool_names_by_class[cls] = tool_name
        return cls

    if tool_cls is None:
        return _register
    return _register(tool_cls)


def ensure_builtin_tools_loaded() -> None:
    """确保内置工具已加载（SDK 薄壳：无内置工具，no-op）。"""
    return None


def get_builtin_tool_class(name: str) -> type | None:
    """按名称取内置工具类（SDK 本地注册表）。"""
    return _builtin_tool_classes_by_name.get(name)


def get_builtin_tool_name(tool_cls: type) -> str | None:
    """按类取内置工具名。"""
    return _builtin_tool_names_by_class.get(tool_cls)


def iter_builtin_tool_classes() -> tuple[type, ...]:
    """迭代全部内置工具类。"""
    return tuple(_builtin_tool_classes_by_name.values())


def get_builtin_tool_config_rule(name: str):
    """取内置工具配置规则（SDK 薄壳：返回 None）。"""
    return None


def get_builtin_tool_config_statuses():
    """取内置工具配置状态（SDK 薄壳：返回空 dict）。"""
    return {}


def get_builtin_tool_config_tags():
    """取内置工具配置标签（SDK 薄壳：返回空 dict）。"""
    return {}


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