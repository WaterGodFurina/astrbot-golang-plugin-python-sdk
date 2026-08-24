"""活跃事件注册表（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.utils.active_event_registry`：
维护 unified_msg_origin → 活跃事件集合，插件在 reset/删除对话等场景
调用 `active_event_registry.stop_all(umo, exclude=message)` 终止该会话
正在处理的事件。

Go 宿主中事件的停止由宿主管线负责，插件侧的停止标记（event.stop_event()）
仍会记录在事件对象上（宿主经 result 的 force_stop 感知）。
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from astrbot.core.platform import AstrMessageEvent


class ActiveEventRegistry:
    """维护 unified_msg_origin 到活跃事件的映射。"""

    def __init__(self) -> None:
        self._events: dict[str, set[Any]] = defaultdict(set)
        self._agent_stop_callbacks: dict[Any, Callable[[], None]] = {}

    def register(self, event: "AstrMessageEvent") -> None:
        """登记一个活跃事件。"""
        umo = getattr(event, "unified_msg_origin", "")
        if umo:
            self._events[umo].add(event)

    def unregister(self, event: "AstrMessageEvent") -> None:
        """注销一个活跃事件。"""
        umo = getattr(event, "unified_msg_origin", "")
        self._agent_stop_callbacks.pop(event, None)
        # defaultdict 的 [] 访问会为未登记的 umo 创建空 set 并永久残留：
        # 用 get 读桶，未登记时直接返回。
        bucket = self._events.get(umo)
        if bucket is not None:
            bucket.discard(event)
            if not bucket:
                del self._events[umo]

    def register_agent_stop_callback(
        self,
        event: "AstrMessageEvent",
        callback: Callable[[], None],
    ) -> None:
        """为活跃事件注册 Agent 取消回调。"""
        self._agent_stop_callbacks[event] = callback

    def unregister_agent_stop_callback(self, event: "AstrMessageEvent") -> None:
        """移除 Agent 取消回调。"""
        self._agent_stop_callbacks.pop(event, None)

    def stop_all(
        self,
        umo: str,
        exclude: "AstrMessageEvent | None" = None,
    ) -> int:
        """终止指定 UMO 的所有活跃事件。

        Args:
            umo: 统一消息来源标识符。
            exclude: 需要排除的事件（通常是发起 reset 的事件本身）。

        Returns:
            被终止的事件数量。
        """
        count = 0
        for event in list(self._events.get(umo, [])):
            if event is not exclude:
                stop = getattr(event, "stop_event", None)
                if callable(stop):
                    stop()
                count += 1
        return count

    def request_agent_stop_all(
        self,
        umo: str,
        exclude: "AstrMessageEvent | None" = None,
    ) -> int:
        """请求停止指定 UMO 的所有活跃事件中的 Agent 运行（不中断事件传播）。"""
        count = 0
        for event in list(self._events.get(umo, [])):
            if event is not exclude:
                set_extra = getattr(event, "set_extra", None)
                if callable(set_extra):
                    set_extra("agent_stop_requested", True)
                callback = self._agent_stop_callbacks.get(event)
                if callback:
                    callback()
                count += 1
        return count


# 全局实例（插件 `from astrbot.core.utils.active_event_registry import
# active_event_registry` 使用）
active_event_registry = ActiveEventRegistry()
