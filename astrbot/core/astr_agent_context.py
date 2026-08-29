"""AstrAgent 运行上下文（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.astr_agent_context` 的类型入口：livingmemory
等插件以 `FunctionTool[AstrAgentContext]` / `ContextWrapper[AstrAgentContext]`
方式把它作为工具的上下文类型参数引用。本运行时中工具实际收到的是
`ContextWrapper`（见 `astrbot.core.agent.run_context`），本类仅承载类型
兼容与基础字段兜底，字段均为可选默认值。
"""

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AstrAgentContext:
    """Agent 级运行上下文（兼容本体字段命名的精简版）。"""

    agent_name: str = ""
    agent_id: str = ""
    conversation_id: str = ""
    session_id: str = ""
    provider_id: str = ""
    model_name: str = ""
    persona_id: str = ""
    max_step: int = 0
    metadata: dict = field(default_factory=dict)
    agent: Optional[Any] = None
