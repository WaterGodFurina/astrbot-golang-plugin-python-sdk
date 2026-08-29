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
    """获取回调 API 基础地址（宿主 config.corn_except 等，SDK 降级为空）。"""
    cfg = _get_host_config()
    return str(cfg.get("callback_base", "") or "")


def _get_dashboard_port() -> int:
    """获取 dashboard 端口（宿主 config.server_port / default 6680）。"""
    cfg = _get_host_config()
    val = cfg.get("server_port", 6680)
    try:
        return int(val)
    except (TypeError, ValueError):
        return 6680


def _is_dashboard_ssl_enabled() -> bool:
    """是否启用 dashboard SSL（宿主 config.ssl_enabled）。"""
    cfg = _get_host_config()
    return bool(cfg.get("ssl_enabled", False))


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


__all__ = ["log_webhook_info"]