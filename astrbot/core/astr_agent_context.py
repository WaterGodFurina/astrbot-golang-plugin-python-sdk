"""Agent 运行上下文（Go 宿主兼容运行时，对齐本体 astr_agent_context）。

`AstrAgentContext` 是主 Agent 的运行上下文类型（context / event / extra），
宿主 Agent 编排链在 SDK 外部维护，本类型供插件在 TYPE_CHECKING 引用与
工具签名标注。`AgentContextWrapper` 别名保持与本体 `ContextWrapper[
AstrAgentContext]` 一致。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from astrbot.core.agent.run_context import ContextWrapper

if TYPE_CHECKING:  # pragma: no cover - 仅类型标注，运行时不导入
    from astrbot.core.platform.astr_message_event import AstrMessageEvent
    from astrbot.core.star.context import Context


@dataclass
class AstrAgentContext:
    """主 Agent 运行上下文（对齐本体 astr_agent_context.AstrAgentContext）。"""

    context: Context | None = None
    """The star context instance (宿主 Context，插件侧为占位)。"""
    event: AstrMessageEvent | None = None
    """The message event associated with the agent context."""
    extra: dict[str, str] = field(default_factory=dict)
    """Customized extra data."""


# AgentContextWrapper 别名：与本体 `ContextWrapper[AstrAgentContext]` 等价。
AgentContextWrapper = ContextWrapper[AstrAgentContext]


__all__ = ["AstrAgentContext", "AgentContextWrapper"]