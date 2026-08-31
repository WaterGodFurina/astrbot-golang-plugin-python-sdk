"""文件令牌服务（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.file_token_service.FileTokenService` 的
接口面（本体方法：register_file / check_token_expired / handle_file）。
令牌表由宿主 Go 侧维护（宿主 dashboard 暴露 /api/file/{token} 通用下载
路由），插件侧经 HostBridge.RegisterFileToken RPC 登记：

- register_file(path) → 经宿主登记并返回令牌；宿主不可用 / 宿主无该
  RPC 时降级返回 None（不抛异常，对齐既有降级）
- get_url_from_file_path(path) → 先登记拿令牌，再拼接宿主
  callback_api_base + "/api/file/{token}"；callback_api_base 未配置时
  返回 None 并记 debug 日志（SDK 扩展方法，本体无此方法）
- check_token_expired / handle_file → 令牌表在宿主侧，SDK 无本地表，
  保持降级占位（check 一律视为过期 / handle 返回 None）

插件侧组件（Image/File/Video.register_to_file_service 等）已各自
降级实现，不依赖本服务返回令牌。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("astrbot")


def _host_bridge():
    """获取宿主桥（薄壳转发入口；不可用返回 None）。"""
    try:
        from astrbot.core.star.context import get_host_bridge

        return get_host_bridge()
    except Exception:
        return None


class FileTokenService:
    """文件令牌服务（真实现：令牌表在宿主侧，经 RegisterFileToken 登记）。"""

    def __init__(self, default_timeout: float = 300) -> None:
        self.default_timeout = default_timeout

    async def check_token_expired(self, file_token: str) -> bool:
        """令牌是否过期（令牌表在宿主侧，SDK 无本地表：一律视为过期）。"""
        return True

    async def register_file(
        self, file_path: str, timeout: float | None = None
    ) -> str | None:
        """注册文件并返回令牌（转发宿主 RegisterFileToken）。

        timeout 为令牌 TTL 秒数（None → 0 → 宿主默认 TTL）。宿主不可用 /
        宿主无该 RPC / 登记失败时返回 None（对齐既有降级，不抛异常）。
        """
        bridge = _host_bridge()
        if bridge is None:
            logger.debug("宿主桥不可用，register_file 降级返回 None")
            return None
        try:
            token = await asyncio.to_thread(
                bridge.register_file_token,
                path=str(file_path),
                timeout_sec=int(timeout) if timeout else 0,
            )
        except Exception as e:
            logger.debug(f"register_file_token 桥接失败（降级返回 None）: {e}")
            return None
        return token or None

    async def get_url_from_file_path(self, path: str) -> str | None:
        """根据文件路径返回可公开访问的 URL：
        ``callback_api_base + /api/file/{token}``。

        先经宿主登记拿令牌（token 表在宿主侧），再读宿主
        callback_api_base 配置拼接下载 URL。令牌登记失败或
        callback_api_base 未配置（宿主不可用）时返回 None 并记 debug 日志。
        """
        token = await self.register_file(path)
        if not token:
            logger.debug(f"get_url_from_file_path({path!r}) 登记失败，返回 None")
            return None
        try:
            from astrbot.core.utils.webhook_utils import _get_callback_api_base

            callback_base = await asyncio.to_thread(_get_callback_api_base)
        except Exception as e:
            logger.debug(f"读取 callback_api_base 失败（无法拼接文件 URL）: {e}")
            return None
        callback_base = str(callback_base or "").rstrip("/")
        if not callback_base:
            logger.debug("callback_api_base 未配置，get_url_from_file_path 返回 None")
            return None
        return f"{callback_base}/api/file/{token}"

    async def handle_file(self, file_token: str) -> str | None:
        """根据令牌获取文件路径（令牌表在宿主侧，SDK 无本地表：返回 None）。"""
        return None

    def __getattr__(self, item: str) -> Any:
        """未知方法降级：返回 no-op 可调用对象，避免插件 AttributeError。"""
        return _noop


async def _noop(*args: Any, **kwargs: Any) -> None:
    """统一 no-op 回调（供 __getattr__ 兜底）。"""
    return None


# 模块级单例（对齐本体 `file_token_service` 全局实例）
file_token_service = FileTokenService()
