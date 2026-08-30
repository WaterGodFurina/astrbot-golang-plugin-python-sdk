"""webhook 工具（Go 宿主兼容运行时，对齐本体 utils.webhook_utils）。

统一 webhook 的配置与授权由宿主 Go 侧维护（WebhookPlatform），SDK 仅提供
`log_webhook_info` 日志函数（打印回调地址），配置面板端口/SSL 从宿主
config 经 HostBridge.GetConfig 读取（薄壳转发）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("astrbot")


def _get_host_config() -> dict:
    """尝试从宿主读取配置（薄壳转发 HostBridge.GetConfig）。"""
    try:
        from astrbot.core.star.context import get_host_bridge

        bridge = get_host_bridge()
        if bridge is not None:
            return bridge.get_config("")
    except Exception:
        pass
    return {}


def _get_callback_api_base() -> str:
    """获取回调 API 基础地址（宿主 config.callback_api_base，SDK 降级为空）。"""
    cfg = _get_host_config()
    return str(cfg.get("callback_api_base", "") or "").rstrip("/")


def _get_dashboard_port() -> int:
    """获取 dashboard 端口（宿主 config.dashboard.port / default 6185）。"""
    cfg = _get_host_config()
    dashboard = cfg.get("dashboard")
    if isinstance(dashboard, dict):
        val = dashboard.get("port")
    else:
        val = None
    try:
        return int(val) if val is not None else 6185
    except (TypeError, ValueError):
        return 6185


def _is_dashboard_ssl_enabled() -> bool:
    """是否启用 dashboard SSL（env 优先，其次宿主 config.dashboard.ssl.enable）。
    """
    import os

    env_ssl = os.environ.get("DASHBOARD_SSL_ENABLE") or os.environ.get(
        "ASTRBOT_DASHBOARD_SSL_ENABLE"
    )
    if env_ssl is not None:
        return env_ssl.strip().lower() in {"1", "true", "yes", "on"}
    cfg = _get_host_config()
    dashboard = cfg.get("dashboard")
    if isinstance(dashboard, dict):
        ssl = dashboard.get("ssl")
        if isinstance(ssl, dict):
            return bool(ssl.get("enable"))
    return False


def log_webhook_info(platform_name: str, webhook_uuid: str) -> None:
    """打印美观的 webhook 信息日志（对齐本体 log_webhook_info）。"""
    callback_base = _get_callback_api_base()

    if not callback_base:
        callback_base = "http(s)://<your-astrbot-domain>"

    if not callback_base.startswith("http"):
        callback_base = f"http(s)://{callback_base}"

    callback_base = callback_base.rstrip("/")
    webhook_url = f"{callback_base}/api/platform/webhook/{webhook_uuid}"
    scheme = "https" if _is_dashboard_ssl_enabled() else "http"

    display_log = (
        "\n====================\n"
        f"🔗 机器人平台 {platform_name} 已启用统一 Webhook 模式\n"
        f"📍 Webhook 回调地址: \n"
        f"   ➜  {scheme}://<your-ip>:{_get_dashboard_port()}/api/platform/webhook/{webhook_uuid}\n"
        f"   ➜  {webhook_url}\n"
        "====================\n"
    )
    logger.info(display_log)


def ensure_platform_webhook_config(platform_cfg: dict) -> bool:
    """为支持统一 webhook 的平台自动生成 webhook_uuid（对齐原版语义）。

    Args:
        platform_cfg: 平台配置字典。

    Returns:
        bool: 生成了 webhook_uuid 返回 True，否则返回 False。
    """
    import uuid

    from astrbot.core.config.default import WEBHOOK_SUPPORTED_PLATFORMS

    pt = str(platform_cfg.get("type", "") or "")
    if pt in WEBHOOK_SUPPORTED_PLATFORMS and not platform_cfg.get("webhook_uuid"):
        platform_cfg["webhook_uuid"] = uuid.uuid4().hex[:16]
        return True
    return False


__all__ = ["ensure_platform_webhook_config", "log_webhook_info"]