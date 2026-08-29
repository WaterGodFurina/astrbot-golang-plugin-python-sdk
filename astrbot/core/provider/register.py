"""Provider 注册（Go 宿主兼容运行时，对齐本体 provider/register）。

- `llm_tools`：LLM 函数工具注册表实例（re-export
  `astrbot.core.provider.func_tool_manager.llm_tools`，同一对象）；
- `register_provider_adapter`：提供商适配器注册装饰器（宿主 Provider 由
  Go 侧实现，本装饰器仅记录元数据，不注册到宿主）。
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Callable

from astrbot.core import logger
from astrbot.core.provider.entities import ProviderMetaData, ProviderType
from astrbot.core.provider.func_tool_manager import llm_tools  # noqa: F401  re-export
from astrbot.core.provider.func_tool_manager import llm_tools as _llm_tools

provider_registry: list[ProviderMetaData] = []
"""维护了通过装饰器注册的 Provider（SDK 本地记录，宿主 Provider 由 Go 实现）"""
provider_cls_map: dict[str, ProviderMetaData] = {}
"""维护了 Provider 类型名称和 ProviderMetadata 的映射"""


def register_provider_adapter(
    provider_type_name: str,
    desc: str,
    provider_type: ProviderType = ProviderType.CHAT_COMPLETION,
    default_config_tmpl: dict | None = None,
    provider_display_name: str | None = None,
):
    """注册提供商适配器的带参装饰器（SDK 本地记录，宿主侧不注册）。"""

    def decorator(cls):
        if provider_type_name in provider_cls_map:
            raise ValueError(
                f"检测到大模型提供商适配器 {provider_type_name} 已经注册，"
                "可能发生了大模型提供商适配器类型命名冲突。",
            )

        if default_config_tmpl:
            if "type" not in default_config_tmpl:
                default_config_tmpl["type"] = provider_type_name
            if "enable" not in default_config_tmpl:
                default_config_tmpl["enable"] = False
            if "id" not in default_config_tmpl:
                default_config_tmpl["id"] = provider_type_name

        pm = ProviderMetaData(
            id="default",
            model=None,
            type=provider_type_name,
            desc=desc,
            provider_type=provider_type,
            cls_type=cls,
            default_config_tmpl=default_config_tmpl,
            provider_display_name=provider_display_name,
        )
        provider_registry.append(pm)
        provider_cls_map[provider_type_name] = pm
        logger.debug("Model provider registered (SDK local): %s", provider_type_name)
        return cls

    return decorator


__all__ = [
    "_llm_tools",
    "llm_tools",
    "provider_cls_map",
    "provider_registry",
    "register_provider_adapter",
]