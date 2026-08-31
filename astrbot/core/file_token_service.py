"""文件令牌服务（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.file_token_service.FileTokenService` 的
接口面（本体方法：register_file / check_token_expired / handle_file）。
SDK 无文件服务基础设施，全部方法降级为不抛异常的空实现：

- register_file(path) → 返回 None（宿主 dashboard 无 /api/file/{token}
  通用下载路由，即使返回令牌也无法被宿主 WebUI 解析；插件发本地文件
  时宿主 pipeline 直接消费本地路径/file:// URI，无需令牌 URL）
- check_token_expired / handle_file → 兼容占位
- get_url_from_file_path 为 SDK 扩展方法（本体无此方法），同样降级

插件侧组件（Image/File/Video.register_to_file_service 等）已各自
降级实现，不依赖本服务返回令牌。
"""
from __future__ import annotations

from typing import Any


class FileTokenService:
    """占位文件令牌服务：不维护令牌表，所有操作降级为 no-op。"""

    def __init__(self, default_timeout: float = 300) -> None:
        self.default_timeout = default_timeout

    async def check_token_expired(self, file_token: str) -> bool:
        """令牌是否过期（SDK 无令牌表，一律视为过期）。"""
        return True

    async def register_file(
        self, file_path: str, timeout: float | None = None
    ) -> str | None:
        """注册文件并返回令牌（SDK 降级：返回 None，不抛异常）。"""
        return None

    async def get_url_from_file_path(self, path: str) -> str | None:
        """根据文件路径返回可公开访问的 URL（SDK 降级：返回 None）。"""
        return None

    async def handle_file(self, file_token: str) -> str | None:
        """根据令牌获取文件路径（SDK 无令牌表：返回 None）。"""
        return None

    def __getattr__(self, item: str) -> Any:
        """未知方法降级：返回 no-op 可调用对象，避免插件 AttributeError。"""
        return _noop


async def _noop(*args: Any, **kwargs: Any) -> None:
    """统一 no-op 回调（供 __getattr__ 兜底）。"""
    return None


# 模块级单例（对齐本体 `file_token_service` 全局实例）
file_token_service = FileTokenService()