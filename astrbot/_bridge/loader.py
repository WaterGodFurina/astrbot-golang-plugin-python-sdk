"""Python 插件加载器：import 插件模块、发现 Star 子类、实例化、生命周期。"""
from __future__ import annotations

import importlib
import logging
import os
import re
import sys
from pathlib import Path

from astrbot._bridge import loop
from astrbot.core.star.context import Context
from astrbot.core.star.star import StarMetadata, star_map, star_registry

logger = logging.getLogger("astrbot.loader")


def sanitize_module_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-]", "_", name)


def load_plugin(plugin_dir: str, context: Context) -> StarMetadata | None:
    """import 插件并实例化其 Star 类。

    返回 StarMetadata（插件模块注册的元数据）；失败抛异常。
    """
    plugin_dir = os.path.abspath(plugin_dir)
    if not os.path.isdir(plugin_dir):
        raise FileNotFoundError(f"插件目录不存在: {plugin_dir}")
    sys.path.insert(0, plugin_dir)

    # 优先 import main.py，其次 import 目录名
    module = None
    if os.path.exists(os.path.join(plugin_dir, "main.py")):
        module = importlib.import_module("main")
    elif os.path.exists(os.path.join(plugin_dir, "__init__.py")):
        module = importlib.import_module(sanitize_module_name(os.path.basename(plugin_dir)))
    else:
        for name in ("main", "plugin", os.path.basename(plugin_dir)):
            if os.path.exists(os.path.join(plugin_dir, f"{name}.py")) or os.path.isdir(
                os.path.join(plugin_dir, name)
            ):
                module = importlib.import_module(sanitize_module_name(name))
                break
    if module is None:
        raise ImportError(f"无法在 {plugin_dir} 找到插件入口（main.py 或同名包）")

    module_name = module.__name__

    # 找到该模块注册的 Star 元数据（__init_subclass__ 注册；@register 补充身份信息）
    metadata = star_map.get(module_name)
    if metadata is None:
        # 兼容：插件在子模块里定义 Star 类
        for m, md in star_map.items():
            if m == module_name or m.startswith(module_name + "."):
                metadata = md
                break
    if metadata is None:
        raise ImportError(
            f"插件 {module_name} 未注册任何 Star 类（请继承 Star 并使用 @register 或自动注册）"
        )

    star_cls = metadata.star_cls_type
    if star_cls is None:
        raise ImportError(f"插件 {module_name} 的 Star 类未被识别")

    # 先设置身份，插件 __init__/get_config() 才能用注册名访问宿主配置
    context.plugin_name = metadata.name or os.path.basename(plugin_dir)
    context.plugin_id = metadata.plugin_id

    # 从宿主拉取插件配置
    try:
        config = context.get_config()
    except Exception as e:
        logger.warning(f"插件 {module_name} 配置拉取失败: {e}")
        config = None

    inst = star_cls(context, config)
    metadata.star_cls = inst
    metadata.module = module
    metadata.root_dir_name = os.path.basename(plugin_dir)

    # 注入 plugin_id（对齐 Python 本体 star_manager 的 setattr）
    plugin_id = metadata.plugin_id
    if hasattr(inst, "plugin_id") is False:
        try:
            setattr(star_cls, "plugin_id", plugin_id)
        except Exception:
            pass
    try:
        setattr(star_cls, "name", metadata.name or "")
    except Exception:
        pass

    if config is not None and not hasattr(inst, "config"):
        inst.config = config

    # 生命周期 initialize()
    init = getattr(inst, "initialize", None)
    if init is not None:
        loop.run_coro(init(), timeout=30)
        logger.info(f"插件 {module_name} initialize() 完成")

    return metadata


def terminate_plugin(metadata: StarMetadata) -> None:
    """调用插件的 terminate()（宿主卸载时）。"""
    inst = metadata.star_cls if metadata else None
    if inst is None:
        return
    for name in ("terminate", "shutdown"):
        fn = getattr(inst, name, None)
        if fn is None:
            continue
        try:
            loop.run_coro(fn(), timeout=10)
        except Exception as e:
            logger.warning(f"插件 {metadata.name} {name}() 失败: {e}")
        break
