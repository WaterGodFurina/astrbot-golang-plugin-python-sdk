"""平台适配器注册表（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.platform.register`：提供
`register_platform_adapter` 装饰器，供插件注册平台适配器时调用。
Go 宿主本身没有适配器注册体系（适配器在宿主侧实现），这里仅做
模块级登记（platform_registry / platform_cls_map），保证插件
装饰器用法的 import 与调用不报错，注册结果对宿主为 no-op。
"""
from __future__ import annotations

import logging

from astrbot.core.platform.platform_metadata import PlatformMetadata

logger = logging.getLogger("astrbot")

# 维护了通过装饰器注册的平台适配器
platform_registry: list[PlatformMetadata] = []
# 维护了平台适配器名称和适配器类的映射
platform_cls_map: dict[str, type] = {}


def register_platform_adapter(
    adapter_name: str,
    desc: str,
    default_config_tmpl: dict | None = None,
    adapter_display_name: str | None = None,
    logo_path: str | None = None,
    support_streaming_message: bool = True,
    i18n_resources: dict[str, dict] | None = None,
    config_metadata: dict | None = None,
):
    """用于注册平台适配器的带参装饰器（签名对齐 Python 本体）。

    Go 宿主不支持插件注册平台适配器，这里仅把元数据登记到模块级
    列表，插件注册时不会报错。

    Args:
        adapter_name: 平台适配器名称（唯一标识）
        desc: 平台适配器描述
        default_config_tmpl: 默认配置模板
        adapter_display_name: 平台适配器显示名称
        logo_path: logo 文件路径（相对插件目录）
        support_streaming_message: 是否支持流式消息
        i18n_resources: 国际化资源
        config_metadata: 配置项元数据（WebUI 表单渲染用）
    """

    def decorator(cls: type) -> type:
        # 宿主不支持注册，同名冲突时直接覆盖旧登记，不抛错
        if default_config_tmpl:
            if "type" not in default_config_tmpl:
                default_config_tmpl["type"] = adapter_name
            if "enable" not in default_config_tmpl:
                default_config_tmpl["enable"] = False
            if "id" not in default_config_tmpl:
                default_config_tmpl["id"] = adapter_name

        # 记录被装饰类所在的模块路径，便于热重载时注销
        module_path = cls.__module__

        pm = PlatformMetadata(
            name=adapter_name,
            description=desc,
            id=adapter_name,
            default_config_tmpl=default_config_tmpl,
            adapter_display_name=adapter_display_name,
            logo_path=logo_path,
            support_streaming_message=support_streaming_message,
            module_path=module_path,
            i18n_resources=i18n_resources,
            config_metadata=config_metadata,
        )
        platform_registry.append(pm)
        platform_cls_map[adapter_name] = cls
        logger.debug("Platform adapter registered: %s", adapter_name)
        return cls

    return decorator


def unregister_platform_adapters_by_module(module_path_prefix: str) -> list[str]:
    """根据模块路径前缀注销平台适配器。

    Args:
        module_path_prefix: 模块路径前缀，如 "data.plugins.my_plugin"

    Returns:
        被注销的平台适配器名称列表
    """
    unregistered: list[str] = []
    to_remove: list[PlatformMetadata] = []

    for pm in platform_registry:
        if pm.module_path and pm.module_path.startswith(module_path_prefix):
            to_remove.append(pm)
            unregistered.append(pm.name)

    for pm in to_remove:
        platform_registry.remove(pm)
        if pm.name in platform_cls_map:
            del platform_cls_map[pm.name]
        logger.debug("平台适配器已注销 (来自模块 %s)", pm.module_path)

    return unregistered
