"""Provider 包（Go 宿主兼容运行时）。

对外暴露 Provider / ProviderType / ProviderMeta 等实体，对齐 Python 本体
`astrbot.core.provider` 的常用导出；Provider 实例数据由宿主提供。
"""
from astrbot.core.provider.entities import (
    LLMResponse,
    ProviderMeta,
    ProviderMetaData,
    ProviderRequest,
    ProviderType,
)
from astrbot.core.provider.provider import (
    Provider,
    STTProvider,
    TTSProvider,
)

__all__ = [
    "LLMResponse",
    "Provider",
    "ProviderMeta",
    "ProviderMetaData",
    "ProviderRequest",
    "ProviderType",
    "STTProvider",
    "TTSProvider",
]
