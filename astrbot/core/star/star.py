"""Star 元数据（Go 宿主兼容运行时）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import Star


# ── plugin-scoped registry（共享 Runtime 兼容桥）─────────────────────
# 保留模块级对象作为 **legacy fallback**：python-grpc 单插件进程（无
# current PluginSession）直接读写这些全局；python-shared 多插件 Runtime 下，
# 业务代码访问 `star_map` / `star_registry` 时经 SessionScopedProxy 路由到
# 当前 PluginSession 持有的独立实例（见 astrbot/_runtime/proxy.py）。
_global_star_registry: list["StarMetadata"] = []
_global_star_map: dict[str, "StarMetadata"] = {}
"""key 是模块路径，__module__"""

from astrbot._runtime.context import resolve_session_scoped  # noqa: E402
from astrbot._runtime.proxy import SessionScopedProxy  # noqa: E402

star_registry = SessionScopedProxy(
    lambda: resolve_session_scoped("star_registry", None),
    _global_star_registry,
    name="star_registry",
)
star_map = SessionScopedProxy(
    lambda: resolve_session_scoped("star_map", None),
    _global_star_map,
    name="star_map",
)


def get_star_map() -> dict[str, "StarMetadata"]:
    """返回当前插件的 star_map；无 session 时返回模块级全局（legacy）。"""
    resolved = resolve_session_scoped("star_map", None)
    return resolved if resolved is not None else _global_star_map


def get_star_registry() -> list["StarMetadata"]:
    """返回当前插件的 star_registry；无 session 时返回模块级全局（legacy）。"""
    resolved = resolve_session_scoped("star_registry", None)
    return resolved if resolved is not None else _global_star_registry


@dataclass
class StarMetadata:
    """插件的元数据。"""

    name: str | None = None
    author: str | None = None
    desc: str | None = None
    short_desc: str | None = None
    version: str | None = None
    repo: str | None = None

    star_cls_type: type["Star"] | None = None
    module_path: str | None = None
    star_cls: "Star" | None = None
    module: ModuleType | None = None
    root_dir_name: str | None = None
    reserved: bool = False
    activated: bool = True

    config: dict | None = None

    star_handler_full_names: list[str] = field(default_factory=list)
    display_name: str | None = None
    logo_path: str | None = None
    support_platforms: list[str] = field(default_factory=list)
    astrbot_version: str | None = None
    i18n: dict[str, dict] = field(default_factory=dict)
    pages: list[dict] = field(default_factory=list)

    @property
    def plugin_id(self) -> str:
        p_name = (self.name or "unknown").lower().replace("/", "_")
        p_author = (self.author or "unknown").lower().replace("/", "_")
        return f"{p_author}/{p_name}"

    def __str__(self) -> str:
        return f"Plugin {self.name} ({self.version}) by {self.author}: {self.desc}"

    def __repr__(self) -> str:
        """对齐本体：repr 与 str 返回同一格式。"""
        return f"Plugin {self.name} ({self.version}) by {self.author}: {self.desc}"
