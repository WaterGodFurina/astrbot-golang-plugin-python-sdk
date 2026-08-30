"""Star Handler 元数据与注册表（Go 宿主兼容运行时）。

与 Python 本体 `astrbot.core.star.star_handler` API 对齐。
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Callable

from astrbot.core.star.filter import HandlerFilter
from astrbot.core.star.star import StarMetadata, star_map


class EventType(enum.Enum):
    """表示一个 AstrBot 内部事件的类型。"""

    OnAstrBotLoadedEvent = enum.auto()
    OnPlatformLoadedEvent = enum.auto()

    AdapterMessageEvent = enum.auto()
    OnWaitingLLMRequestEvent = enum.auto()
    OnLLMRequestEvent = enum.auto()
    OnLLMResponseEvent = enum.auto()
    OnAgentBeginEvent = enum.auto()
    OnAgentDoneEvent = enum.auto()
    OnDecoratingResultEvent = enum.auto()
    OnCallingFuncToolEvent = enum.auto()
    OnUsingLLMToolEvent = enum.auto()
    OnLLMToolRespondEvent = enum.auto()
    OnAfterMessageSentEvent = enum.auto()
    OnPluginErrorEvent = enum.auto()
    OnPluginLoadedEvent = enum.auto()
    OnPluginUnloadedEvent = enum.auto()


# 事件类型 → 宿主 hook event 名（与 Go SDK EventOn* 常量一致）
EVENT_TYPE_TO_HOOK_NAME: dict[EventType, str] = {
    EventType.AdapterMessageEvent: "on_message",
    EventType.OnWaitingLLMRequestEvent: "on_waiting_llm_request",
    EventType.OnLLMRequestEvent: "on_llm_request",
    EventType.OnLLMResponseEvent: "on_llm_response",
    EventType.OnAgentBeginEvent: "on_agent_begin",
    EventType.OnAgentDoneEvent: "on_agent_done",
    EventType.OnDecoratingResultEvent: "on_decorating_result",
    EventType.OnCallingFuncToolEvent: "on_tool_call",
    EventType.OnUsingLLMToolEvent: "on_using_llm_tool",
    EventType.OnLLMToolRespondEvent: "on_llm_tool_respond",
    EventType.OnAfterMessageSentEvent: "on_after_message_sent",
    EventType.OnPluginErrorEvent: "on_plugin_error",
    EventType.OnPluginLoadedEvent: "on_plugin_loaded",
    EventType.OnPluginUnloadedEvent: "on_plugin_unloaded",
    EventType.OnAstrBotLoadedEvent: "on_astrbot_loaded",
    EventType.OnPlatformLoadedEvent: "on_platform_loaded",
}


@dataclass
class StarHandlerMetadata:
    """描述一个 Star 所注册的某一个 Handler。"""

    event_type: EventType
    handler_full_name: str
    handler_name: str
    handler_module_path: str
    handler: Callable
    event_filters: list = field(default_factory=list)
    desc: str = ""
    extras_configs: dict = field(default_factory=dict)
    enabled: bool = True

    def __lt__(self, other: "StarHandlerMetadata"):
        return self.extras_configs.get("priority", 0) < other.extras_configs.get(
            "priority", 0
        )


class StarHandlerRegistry:
    def __init__(self) -> None:
        self.star_handlers_map: dict[str, StarHandlerMetadata] = {}
        self._handlers: list[StarHandlerMetadata] = []

    def inject_host_commands(
        self,
        commands_by_plugin: dict[str, list[dict]],
        star_meta_by_plugin: dict[str, dict],
    ) -> None:
        """注入宿主全部插件的命令为虚拟 handler（helps 类插件跨进程枚举指令）。

        子进程架构下每个插件独立进程，本进程注册表只含自己的 handler；
        helps 类插件遍历注册表收集"全部插件的指令"只能看到自己。这里把
        宿主经现有 GetPluginRegistry 通道（每插件带 commands）聚合的全局命令构造为
        带 `virtual` 标记的虚拟 handler 注入注册表——dispatch 的 Register
        跳过虚拟条目（不污染管线），helps 类插件遍历时能看到全部指令。

        只在插件注册完成后调用一次（0 运行期开销；命令随插件重载/宿主
        重启更新）。
        """
        from astrbot.core.star.filter.command import CommandFilter
        from astrbot.core.star.filter.permission import (
            PermissionType,
            PermissionTypeFilter,
        )

        virtual: list[StarHandlerMetadata] = []
        for plugin_id, descs in (commands_by_plugin or {}).items():
            if not descs:
                continue
            sm = star_meta_by_plugin.get(plugin_id) or {}
            module_path = "data.plugins." + plugin_id
            if module_path not in star_map:
                display_name = str(
                    sm.get("display_name") or sm.get("name") or plugin_id
                )
                star_map[module_path] = StarMetadata(
                    name=display_name,
                    display_name=display_name,
                    desc=str(sm.get("desc") or ""),
                    activated=True,
                )
            for d in descs:
                if not isinstance(d, dict) or not d.get("enabled", True):
                    continue
                cmd = str(d.get("command") or "")
                if not cmd or d.get("command_type") == "group":
                    continue
                aliases = set(d.get("aliases") or [])
                parents = (
                    [str(d["parent_group"])] if d.get("parent_group") else []
                )
                filters: list = [
                    CommandFilter(
                        cmd,
                        alias=aliases,
                        parent_command_names=parents or [""],
                    )
                ]
                perm = str(d.get("permission") or "")
                if perm == "admin":
                    filters.append(PermissionTypeFilter(PermissionType.ADMIN))
                elif perm == "member":
                    filters.append(PermissionTypeFilter(PermissionType.MEMBER))
                virtual.append(
                    StarHandlerMetadata(
                        event_type=EventType.AdapterMessageEvent,
                        handler_full_name=(
                            "host_virtual_" + plugin_id + "_" + cmd
                        ),
                        handler_name=cmd,
                        handler_module_path=module_path,
                        handler=lambda *a, **k: None,  # 占位：仅被 metadata 读取
                        event_filters=filters,
                        desc=str(d.get("description") or ""),
                        extras_configs={"virtual": True},
                        enabled=True,
                    )
                )
        keep = [
            h
            for h in self._handlers
            if not h.extras_configs.get("virtual")
        ]
        keep.extend(virtual)
        self.replace_all(keep)

    def append(self, handler: StarHandlerMetadata) -> None:
        if "priority" not in handler.extras_configs:
            handler.extras_configs["priority"] = 0
        self.star_handlers_map[handler.handler_full_name] = handler
        self._handlers.append(handler)
        self._handlers.sort(key=lambda h: -h.extras_configs["priority"])

    def get_handler_by_full_name(self, name: str) -> StarHandlerMetadata | None:
        return self.star_handlers_map.get(name)

    def get_handlers_by_event_type(self, event_type: EventType):
        return [
            h
            for h in self._handlers
            if h.event_type == event_type
            and h.enabled
            and (h.extras_configs.get("activated", True) is not False)
        ]

    def get_handlers_by_module_name(
        self,
        module_name: str,
    ) -> list["StarHandlerMetadata"]:
        """按 handler 模块路径取全部 handler（对齐本体 StarHandlerRegistry）。"""
        return [
            handler
            for handler in self._handlers
            if handler.handler_module_path == module_name
        ]

    def remove(self, handler: "StarHandlerMetadata") -> None:
        """从注册表移除指定 handler（对齐本体 StarHandlerRegistry.remove）。"""
        self.star_handlers_map.pop(handler.handler_full_name, None)
        self._handlers = [h for h in self._handlers if h != handler]

    def all(self) -> list[StarHandlerMetadata]:
        return list(self._handlers)

    def __iter__(self):
        return iter(self._handlers)

    def __len__(self) -> int:
        return len(self._handlers)

    def clear(self) -> None:
        self._handlers.clear()
        self.star_handlers_map.clear()

    def replace_all(self, handlers: list[StarHandlerMetadata]) -> None:
        """整体替换注册表内容（保留 priority 排序语义）。

        用于刷新宿主全局命令的虚拟条目：先移除旧虚拟 handler，再追加新
        虚拟 handler（真实 handler 保持不变，见 host_commands.sync_host_commands）。
        """
        for h in handlers:
            if "priority" not in h.extras_configs:
                h.extras_configs["priority"] = 0
        self._handlers = list(handlers)
        self._handlers.sort(key=lambda h: -h.extras_configs["priority"])
        self.star_handlers_map = {
            h.handler_full_name: h for h in self._handlers
        }


star_handlers_registry = StarHandlerRegistry()
"""全局 Star Handler 注册表"""


def is_virtual_handler(md: StarHandlerMetadata) -> bool:
    """虚拟（宿主全局命令）handler 标记。"""
    return bool(md.extras_configs.get("virtual"))
