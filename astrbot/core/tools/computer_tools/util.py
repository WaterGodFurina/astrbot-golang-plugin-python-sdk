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


def check_admin_permission(context: ContextWrapper, operation_name: str) -> str | None:
    """检查管理员权限（对齐本体：非 None 返回错误消息 -> 拒绝操作）。

    SDK 薄壳：转发宿主 event 的管理员状态；无事件/非管理员时返回本体的
    标准拒绝消息，否则返回 None（允许）。
    """
    ctx = getattr(context, "context", None)
    is_admin = False
    sender_id = ""
    if ctx is not None:
        event = getattr(ctx, "event", None)
        if event is not None:
            is_admin = bool(getattr(event, "is_admin", False))
            sender_id = str(getattr(event, "get_sender_id", lambda: "")() or "")
    if is_admin:
        return None
    return (
        f"error: Permission denied. {operation_name} is only allowed for admin users. "
        "Tell user to set admins in `AstrBot WebUI -> Config -> General Config` by adding their user ID to the admins list if they need this feature. "
        f"User's ID is: {sender_id}. User's ID can be found by using /sid command."
    )


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