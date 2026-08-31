"""指令配置同步（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot/core/star/command_management.py` 的公开符号面：
- `CommandDescriptor` 指令描述数据类（字段与本体一致）；
- `sync_command_configs` / `toggle_command` / `rename_command` /
  `update_command_permission` / `list_commands` / `list_command_conflicts`。

SDK 降级说明：指令配置的收集/启停/重命名在 Go 宿主中由原生能力处理
（internal/star/command_descriptors.go CollectCommandDescriptors 对应
本体 list_commands 语义），SDK 侧仅保证 import 面与签名可用——查询类
函数返回空结果并记录警告，写操作抛出 RuntimeError。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("astrbot")


@dataclass
class CommandDescriptor:
    """描述一个指令（对齐本体 command_management.CommandDescriptor 字段）。"""

    handler: Any = None
    filter_ref: Any | None = None
    handler_full_name: str = ""
    handler_name: str = ""
    plugin_name: str = ""
    plugin_display_name: str | None = None
    module_path: str = ""
    description: str = ""
    command_type: str = "command"
    raw_command_name: str | None = None
    current_fragment: str | None = None
    parent_signature: str = ""
    parent_group_handler: str = ""
    original_command: str | None = None
    effective_command: str | None = None
    aliases: list[str] = field(default_factory=list)
    permission: str = "everyone"
    enabled: bool = True
    is_group: bool = False
    is_sub_command: bool = False
    reserved: bool = False
    config: Any | None = None
    has_conflict: bool = False
    sub_commands: list["CommandDescriptor"] = field(default_factory=list)


async def sync_command_configs() -> None:
    """同步指令配置，清理过期配置（SDK 降级：no-op，宿主原生维护）。"""


async def toggle_command(handler_full_name: str, enabled: bool) -> CommandDescriptor:
    """启停指定指令（签名对齐本体，写操作由宿主处理）。

    Raises:
        RuntimeError: 指令启停由 Go 宿主原生处理。
    """
    raise RuntimeError(
        "SDK 不支持启停指令：指令配置由 Go 宿主原生管理，请使用宿主管理界面。"
    )


async def rename_command(
    handler_full_name: str,
    new_fragment: str,
    aliases: list[str] | None = None,
) -> CommandDescriptor:
    """重命名指定指令（签名对齐本体，写操作由宿主处理）。

    Raises:
        RuntimeError: 指令重命名由 Go 宿主原生处理。
    """
    raise RuntimeError(
        "SDK 不支持重命名指令：指令配置由 Go 宿主原生管理，请使用宿主管理界面。"
    )


async def update_command_permission(
    handler_full_name: str,
    permission_type: str,
) -> CommandDescriptor:
    """更新指定指令的权限（签名对齐本体，写操作由宿主处理）。

    Raises:
        RuntimeError: 指令权限管理由 Go 宿主原生处理。
    """
    raise RuntimeError(
        "SDK 不支持修改指令权限：指令配置由 Go 宿主原生管理，请使用宿主管理界面。"
    )


async def list_commands() -> list[dict[str, Any]]:
    """列出全部指令（SDK 降级：指令清单由宿主 WebUI 提供时返回空列表）。"""
    logger.debug("list_commands 降级：指令清单由 Go 宿主原生维护")
    return []


async def list_command_conflicts() -> list[dict[str, Any]]:
    """列出冲突指令组（SDK 降级：冲突检测由宿主原生维护，返回空列表）。"""
    logger.debug("list_command_conflicts 降级：冲突检测由 Go 宿主原生维护")
    return []
