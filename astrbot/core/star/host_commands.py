"""宿主全局命令 → 本进程 star 注册表的虚拟条目注入（Go 宿主兼容运行时）。

子进程架构下每个 Python 插件独立进程，`star_handlers_registry` 只含本进程
注册的 handler。helps 类插件（helps_image）遍历注册表收集"全部插件的指令"
只能看到自己——原版所有插件共享同一进程注册表。

这里从宿主 HostService.ListCommandDescriptors RPC 拉取全部插件的命令
描述符，构造"虚拟 handler"（带 `extras_configs["virtual"]=True` 标记）注入
本进程注册表；dispatch 的 Register 跳过虚拟条目（不注册进 commands/
filters，避免管线匹配到无真实 handler 的虚拟命令），而 helps 类插件遍历
注册表时能看到全部插件的指令。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.permission import (
    PermissionType,
    PermissionTypeFilter,
)
from astrbot.core.star.star import StarMetadata, star_map
from astrbot.core.star.star_handler import (
    EventType,
    StarHandlerMetadata,
    star_handlers_registry,
)

logger = logging.getLogger("astrbot")

# 虚拟条目刷新 TTL：helps 类插件渲染前 HandleCommand 触发一次同步，
# 缓存几秒避免每次消息都拉宿主 RPC。
HOST_COMMANDS_TTL = 5.0

_lock = threading.Lock()
_last_sync_at = 0.0


def is_virtual_handler(md: StarHandlerMetadata) -> bool:
    """虚拟（宿主全局命令）handler 标记。"""
    return bool(md.extras_configs.get("virtual"))


def _build_virtual_handlers(
    descs: list[dict], stars: list[dict]
) -> list[StarHandlerMetadata]:
    """宿主命令描述符 → 虚拟 handler 列表（按插件分组填充 star_map）。"""
    star_by_id: dict[str, dict] = {}
    for s in stars:
        sid = str(s.get("id") or "") or str(s.get("name") or "")
        if sid:
            star_by_id[sid] = s

    out: list[StarHandlerMetadata] = []
    for d in descs:
        if not isinstance(d, dict):
            continue
        if not d.get("enabled", True):
            continue
        plugin_id = str(d.get("plugin_name") or "")
        if not plugin_id:
            continue  # 内置指令（归属宿主自身）不注入
        cmd = str(d.get("command") or "")
        if not cmd or d.get("command_type") == "group":
            continue  # 组命令由子命令展示

        aliases = set(d.get("aliases") or [])
        parents = [str(d["parent_group"])] if d.get("parent_group") else []

        filters: list[Any] = [
            CommandFilter(
                cmd,
                alias=aliases,
                parent_command_names=parents or [""],
            )
        ]
        perm = str(d.get("permission") or "")
        if perm == "admin":
            filters.append(PermissionTypeFilter(PermissionType.ADMIN))
        elif perm == "member":
            filters.append(PermissionTypeFilter(PermissionType.MEMBER))

        # star_map 虚拟元数据：helps 类插件用 handler_module_path 查插件名。
        module_path = "data.plugins." + plugin_id
        if module_path not in star_map:
            sm = star_by_id.get(plugin_id) or {}
            display_name = str(
                sm.get("display_name") or sm.get("name") or plugin_id
            )
            star_map[module_path] = StarMetadata(
                name=display_name,
                display_name=display_name,
                desc=str(sm.get("desc") or ""),
                activated=True,
            )

        out.append(
            StarHandlerMetadata(
                event_type=EventType.AdapterMessageEvent,
                handler_full_name="host_virtual_" + plugin_id + "_" + cmd,
                handler_name=cmd,
                handler_module_path=module_path,
                handler=lambda *a, **k: None,  # 占位：仅被 metadata 读取
                event_filters=filters,
                desc=str(d.get("description") or ""),
                extras_configs={"virtual": True},
                enabled=True,
            )
        )
    return out


def sync_host_commands(bridge_getter: Any) -> None:
    """从宿主拉取全局命令描述符，刷新注册表中的虚拟条目。

    bridge_getter: 返回 HostBridge 实例的可调用对象（或 HostBridge 本身）。
    TTL 内不重复拉取；宿主不支持（旧版无该 RPC）时静默跳过。
    """
    global _last_sync_at
    now = time.monotonic()
    if now - _last_sync_at < HOST_COMMANDS_TTL:
        return
    _last_sync_at = now

    try:
        bridge = bridge_getter() if callable(bridge_getter) else bridge_getter
        descs = bridge.list_command_descriptors()
        stars = bridge.list_stars()
    except Exception as e:
        logger.debug(f"宿主全局命令同步失败（宿主可能不支持）: {e}")
        return

    virtual = _build_virtual_handlers(descs, stars)
    with _lock:
        keep = [
            h
            for h in star_handlers_registry.all()
            if not is_virtual_handler(h)
        ]
        star_handlers_registry.replace_all(keep + virtual)


def sync_host_commands_once(bridge_getter: Any) -> None:
    """无条件同步一次（忽略 TTL），供测试/启动注入。"""
    global _last_sync_at
    _last_sync_at = 0.0
    sync_host_commands(bridge_getter)