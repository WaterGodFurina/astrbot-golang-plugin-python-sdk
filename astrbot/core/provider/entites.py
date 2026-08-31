"""`entites.py` 兼容别名（对齐本体同名文件，历史拼写保留）。

本体 astrbot-py 中 `astrbot/core/provider/entites.py`（少一个 i）是
entities.py 的旧别名文件，长期被插件以
`from astrbot.core.provider.entites import LLMResponse` 方式引用。
SDK 补齐同一导出面，避免老插件 ModuleNotFoundError。
"""
from astrbot.core.agent.message import (
    AssistantMessageSegment,
    ToolCallMessageSegment,
)
from astrbot.core.provider.entities import (
    LLMResponse,
    ProviderMetaData,
    ProviderRequest,
    ProviderType,
    ToolCallsResult,
)

__all__ = [
    "AssistantMessageSegment",
    "LLMResponse",
    "ProviderMetaData",
    "ProviderRequest",
    "ProviderType",
    "ToolCallMessageSegment",
    "ToolCallsResult",
]
