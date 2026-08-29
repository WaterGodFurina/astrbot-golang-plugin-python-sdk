"""computer_tools 工具（Go 宿主兼容运行时，对齐本体 computer_tools.util）。

SDK 薄壳：工作区路径解析与权限检测在插件侧提供轻量实现；真正执行由宿主
sandbox（Docker/LocalBooter/ShipyardNeo）原生完成，本模块仅保证 import。
"""
from __future__ import annotations

import os
from pathlib import Path

from astrbot.core.agent.run_context import ContextWrapper, TContext  # noqa: F401


def workspace_root(umo: str) -> Path:
    """每个 umo 的工作区根（宿主 sandbox 原生维护；SDK 给出宿主 data 下的占位路径）。"""
    data_dir = os.environ.get("ASTRBOT_DATA_PATH", str(Path.cwd() / "data"))
    base = Path(data_dir) / "temp" / "workspaces"
    safe = (umo or "default").replace("/", "_").replace("\\", "_")
    return base / (safe or "default")


async def workspace_root_for_context(context: ContextWrapper) -> Path:
    """从上下文解析工作区根。"""
    umo = ""
    ctx = getattr(context, "context", None)
    if ctx is not None:
        event = getattr(ctx, "event", None)
        if event is not None:
            umo = getattr(event, "unified_msg_origin", "") or ""
    return workspace_root(umo)


def is_local_runtime(context: ContextWrapper) -> bool:
    """是否为受限本地运行环境（SDK 薄壳：恒 False）。"""
    return False


def check_admin_permission(context: ContextWrapper, user_id: str) -> bool:
    """检查管理员权限（SDK 薄壳：转发宿主 event.is_admin，缺省 False）。"""
    ctx = getattr(context, "context", None)
    if ctx is None:
        return False
    event = getattr(ctx, "event", None)
    if event is None:
        return False
    return bool(getattr(event, "is_admin", False))


def normalize_umo_for_workspace(umo: str) -> str:
    """把 umo 归一化为安全的目录名（对齐本体 computer_tools.util）。"""
    return (umo or "").replace(":", "_").replace("/", "_").replace("!", "_")


__all__ = [
    "ContextWrapper",
    "TContext",
    "check_admin_permission",
    "is_local_runtime",
    "normalize_umo_for_workspace",
    "workspace_root",
    "workspace_root_for_context",
]