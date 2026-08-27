# 兼容导出: Provider 从 provider 模块重新导出（对齐本体）
from astrbot.core.provider import Provider

from .base import Star
from .context import Context, get_host_bridge, set_host_bridge
from .star import StarMetadata, star_map, star_registry
from .star_handler import EventType, StarHandlerMetadata, star_handlers_registry
from .star_manager import PluginManager
from .star_tools import StarTools

__all__ = [
    "Context",
    "EventType",
    "PluginManager",
    "Provider",
    "Star",
    "StarHandlerMetadata",
    "StarMetadata",
    "StarTools",
    "get_host_bridge",
    "set_host_bridge",
    "star_handlers_registry",
    "star_map",
    "star_registry",
]
