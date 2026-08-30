"""平台管理器（Go 宿主兼容运行时，对齐本体 platform/manager）。

本体的 PlatformManager 负责实例化/启停平台适配器；Go 宿主中平台由宿主
侧原生管理（管道/事件循环在宿主内运行），插件侧仅需读取宿主的平台清单
（经 HostService.ListPlatforms 薄壳转发，见 context._PlatformManagerStub）。
此处提供同名公开类与本体参数兼容的构造，插件 `from astrbot.core.platform.
manager import PlatformManager` 可 import 与 isinstance 引用。
"""
from __future__ import annotations

from typing import Any

from astrbot.core.platform.platform import Platform, PlatformStatus  # noqa: F401  re-export
from astrbot.core.star.context import _PlatformManagerStub  # noqa: F401 复用宿主转发实现


class PlatformManager(_PlatformManagerStub):
    """平台管理器（SDK 薄壳：平台清单读取转宿主 ListPlatforms）。

    构造参数兼容本体（config / event_queue 均被忽略——宿主平台原生运行，
    插件侧不实例化平台）。
    """

    def __init__(self, config: Any = None, event_queue: Any = None) -> None:
        super().__init__()
        self.config = config
        self.event_queue = event_queue
        self.settings = getattr(config, "get", lambda k, d=None: d)("platform_settings") if config else None

    async def initialize(self) -> None:
        """初始化所有平台（SDK 薄壳：宿主平台原生运行，no-op）。"""

    async def terminate(self) -> None:
        """终止所有平台（SDK 薄壳：宿主侧原生，no-op）。"""

    def get_insts(self) -> list:
        """获取平台实例列表（从宿主 ListPlatforms 惰性拉取）。"""
        return super().get_insts()

    def get_all_stats(self) -> dict:
        """全部平台统计（SDK 薄壳：宿主经 CallAction 提供，此处返回空）。"""
        return {}

    async def load_platform(self, platform_config: dict) -> None:
        """实例化一个平台（SDK 薄壳：宿主平台原生运行，no-op）。"""

    async def reload(self, platform_config: dict) -> None:
        """重载一个平台（SDK 薄壳：宿主侧原生，no-op）。"""

    async def terminate_platform(self, platform_id: str) -> None:
        """终止一个平台（SDK 薄壳：宿主侧原生，no-op）。"""


__all__ = ["Platform", "PlatformManager", "PlatformStatus"]