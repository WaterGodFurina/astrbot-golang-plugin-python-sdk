"""computer_tools 工具（Go 宿主兼容运行时，对齐本体 computer_tools/util）。

对齐本体 ``astrbot.core.tools.computer_tools.util`` 的四个公共函数签名与
语义；真实读写/执行由宿主 sandbox（Docker/LocalBooter/ShipyardNeo）原生
完成，本模块仅提供路径解析与权限判定，保证插件 import 与调用面可用。

与本体的差异（宿主边界）：
- ``get_config`` 来自 SDK Context（AstrBotConfig，dict 子类），读取
  ``provider_settings.computer_use_runtime`` / ``computer_use_require_admin``
  的键路径与本体一致；
- ``workspace_root`` 基于 SDK 的 ``get_astrbot_workspaces_path()``（宿主
  ASTRBOT_DATA_PATH/workspaces），目录名归一化与本体同一规则。
"""
from __future__ import annotations

import re
from pathlib import Path

from astrbot.core.agent.run_context import ContextWrapper, TContext  # noqa: F401
from astrbot.core.utils.astrbot_path import get_astrbot_workspaces_path


def workspace_root(umo: str) -> Path:
    """返回 legacy 的 per-session 工作区根（对齐本体 workspace_root）。

    Args:
        umo: 统一消息源（unified message origin）。

    Returns:
        Path: ``workspaces/<normalized_umo>``（不做严格解析）。
    """
    return (
        Path(get_astrbot_workspaces_path()) / normalize_umo_for_workspace(umo)
    ).resolve(strict=False)


async def workspace_root_for_context(context: ContextWrapper) -> Path:
    """从工具调用上下文解析工作区根（签名对齐本体 workspace_root_for_context）。

    本体在 ``context.context.context._db`` 为 BaseDatabase 时走
    ``resolve_workspace_root_for_umo``；SDK 无该解析通道，恒走 legacy
    ``workspace_root``（与本体 DB 缺失时的回退分支一致）。
    """
    umo = ""
    ctx = getattr(context, "context", None)
    if ctx is not None:
        event = getattr(ctx, "event", None)
        if event is not None:
            umo = getattr(event, "unified_msg_origin", "") or ""
    return workspace_root(umo)


def _get_provider_settings(context: ContextWrapper) -> dict:
    """读取上下文中的 provider_settings（读不到时返回空 dict）。"""
    try:
        cfg = context.context.context.get_config(
            umo=context.context.event.unified_msg_origin
        )
        return cfg.get("provider_settings", {}) or {}
    except Exception:
        return {}


def is_local_runtime(context: ContextWrapper) -> bool:
    """是否为本地运行时（对齐本体：读 computer_use_runtime，默认 local）。"""
    provider_settings = _get_provider_settings(context)
    runtime = str(provider_settings.get("computer_use_runtime", "local"))
    return runtime == "local"


def check_admin_permission(
    context: ContextWrapper, operation_name: str
) -> str | None:
    """检查管理员权限（签名与拒绝文案对齐本体 check_admin_permission）。

    本体语义：``provider_settings.computer_use_require_admin``（默认 True）
    且事件角色非 admin 时拒绝；返回 None 表示放行。上下文不可读时按
    默认配置拒绝（与本体 require_admin 默认值一致）。
    """
    provider_settings = _get_provider_settings(context)
    require_admin = provider_settings.get("computer_use_require_admin", True)

    event = None
    ctx = getattr(context, "context", None)
    if ctx is not None:
        event = getattr(ctx, "event", None)
    role = getattr(event, "role", None)
    if require_admin and role != "admin":
        sender_id = ""
        if event is not None:
            try:
                sender_id = str(event.get_sender_id() or "")
            except Exception:
                sender_id = ""
        return (
            f"error: Permission denied. {operation_name} is only allowed for admin users. "
            "Tell user to set admins in `AstrBot WebUI -> Config -> General Config` by adding their user ID to the admins list if they need this feature. "
            f"User's ID is: {sender_id}. User's ID can be found by using /sid command."
        )
    return None


def normalize_umo_for_workspace(umo: str) -> str:
    """把 umo 归一化为文件系统安全目录名（对齐本体 workspace.py:22-32）。

    非 ``[A-Za-z0-9._-]`` 的连续字符折叠为单个 ``_``；归一化结果为空时
    返回 ``"unknown"``。
    """
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", (umo or "").strip())
    return normalized or "unknown"


__all__ = [
    "ContextWrapper",
    "TContext",
    "check_admin_permission",
    "is_local_runtime",
    "normalize_umo_for_workspace",
    "workspace_root",
    "workspace_root_for_context",
]
