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

# 插件生命周期超时（秒），可用环境变量覆盖：initialize() 与 terminate() 里
# 插件可能做网络拉取/模型调用等耗时操作，默认值取大些避免误杀；热路径
# （正常消息处理）不涉及这些调用。
INIT_TIMEOUT = float(os.environ.get("ASTRBOT_PLUGIN_INIT_TIMEOUT", "60"))
TERM_TIMEOUT = float(os.environ.get("ASTRBOT_PLUGIN_TERM_TIMEOUT", "30"))


def sanitize_module_name(name: str) -> str:
    """目录名 → 合法 Python 模块名。

    只保留字母/数字/下划线：`-`（GitHub 仓库目录如
    `astrbot-plugin-qq-group-daily-analysis-4.11.2-<commit>`）与 `.`
    （版本号会破坏模块层级）等非法字符一律替换为 `_`。否则 main.py 的
    相对导入（from .src... import）会因模块名含 `-`/`.` 而
    ModuleNotFoundError（模块系统无法处理）。
    """
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def _load_package_plugin(plugin_dir: str, pkg_name: str):
    """包式插件加载（插件目录含 __init__.py，Python AstrBot 生态惯例）：

    把插件目录作为包（<sanitized 目录名>），main.py 作为包内子模块加载，
    使 main.py 中的相对导入（from .backend.xxx import ...）可用。
    返回 main 模块。
    """
    import importlib.util

    pkg = sanitize_module_name(pkg_name)
    if not pkg or not re.match(r"^[A-Za-z_]\w*$", pkg):
        # 目录名不适合做模块名（数字开头等）→ 加前缀
        pkg = "astrbot_plugin_" + pkg

    init_path = os.path.join(plugin_dir, "__init__.py")
    pkg_spec = importlib.util.spec_from_file_location(
        pkg, init_path, submodule_search_locations=[plugin_dir]
    )
    if pkg_spec is None or pkg_spec.loader is None:
        raise ImportError(f"无法加载插件包 {pkg_name}")
    pkg_mod = importlib.util.module_from_spec(pkg_spec)
    sys.modules[pkg] = pkg_mod
    pkg_spec.loader.exec_module(pkg_mod)

    main_path = os.path.join(plugin_dir, "main.py")
    main_spec = importlib.util.spec_from_file_location(
        f"{pkg}.main", main_path, submodule_search_locations=None
    )
    if main_spec is None or main_spec.loader is None:
        raise ImportError(f"插件 {pkg_name} 缺少 main.py")
    main_mod = importlib.util.module_from_spec(main_spec)
    sys.modules[f"{pkg}.main"] = main_mod
    main_spec.loader.exec_module(main_mod)
    return main_mod


def _load_namespace_package_plugin(plugin_dir: str, pkg_name: str):
    """无 __init__.py 的插件目录按 namespace package 加载（Python 3.3+）。

    Python AstrBot 生态存在无 __init__.py 但仍用相对导入的插件（如 box 的
    `from .core.draw import ...`）；把它们作为 <目录名>.main 的 namespace 包
    子模块加载，相对导入即可解析（core 等子目录同样按 namespace 包发现）。
    """
    import importlib.util
    import types

    pkg = sanitize_module_name(pkg_name)
    if not pkg or not re.match(r"^[A-Za-z_]\w*$", pkg):
        pkg = "astrbot_plugin_" + pkg

    # 显式注册 namespace 包：main.py 的相对导入（from .src... import）依赖
    # 父包在 sys.modules 中且 __path__ 指向插件目录。不能依赖 sys.path 的
    # 目录名自动发现——GitHub 仓库式目录名（astrbot-plugin-xxx-<version>-
    # <commit>）经 sanitize 后的模块名与磁盘目录名不一致，自动发现会失败
    # （ModuleNotFoundError）。
    pkg_mod = types.ModuleType(pkg)
    pkg_mod.__path__ = [plugin_dir]
    sys.modules[pkg] = pkg_mod

    main_path = os.path.join(plugin_dir, "main.py")
    main_spec = importlib.util.spec_from_file_location(
        f"{pkg}.main", main_path, submodule_search_locations=None
    )
    if main_spec is None or main_spec.loader is None:
        raise ImportError(f"插件 {pkg_name} 缺少 main.py")
    main_mod = importlib.util.module_from_spec(main_spec)
    sys.modules[f"{pkg}.main"] = main_mod
    main_spec.loader.exec_module(main_mod)
    return main_mod


def _apply_yaml_metadata(plugin_dir: str, metadata: StarMetadata) -> None:
    """读取插件 metadata.yaml 覆盖身份字段（Python AstrBot 本体语义：yaml
    元数据优先于 @register 装饰器；无 @register 的插件（__init_subclass__ 自动
    注册）只有 yaml 提供 name/desc/version/author 等）。"""
    import json

    for name in ("metadata.yaml", "metadata.yml", "metadata.json"):
        path = os.path.join(plugin_dir, name)
        if not os.path.exists(path):
            continue
        try:
            if name.endswith(".json"):
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            else:
                import yaml

                with open(path, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning(f"读取 {name} 失败: {e}")
            return
        if not isinstance(data, dict):
            return
        if data.get("name"):
            metadata.name = str(data["name"]).strip()
        if data.get("author"):
            metadata.author = str(data["author"]).strip()
        if data.get("version"):
            metadata.version = str(data["version"]).strip()
        if data.get("desc"):
            metadata.desc = str(data["desc"]).strip()
        if data.get("short_desc"):
            metadata.short_desc = str(data["short_desc"]).strip()
        if data.get("display_name"):
            metadata.display_name = str(data["display_name"]).strip()
        if data.get("repo"):
            metadata.repo = str(data["repo"]).strip()
        break


def load_plugin_import(plugin_dir: str, context: Context) -> StarMetadata | None:
    """阶段 A：import 插件模块并发现 Star 类（不实例化）。

    gRPC server 先于插件加载启动，宿主 Register 需要等本阶段完成（star
    注册表填充）；而实例化（__init__ 里同步调 get_config）需要等宿主
    Register 完成（HostService 身份绑定）。两阶段拆分避免循环等待。
    """
    plugin_dir = os.path.abspath(plugin_dir)
    if not os.path.isdir(plugin_dir):
        raise FileNotFoundError(f"插件目录不存在: {plugin_dir}")

    is_package = os.path.exists(os.path.join(plugin_dir, "__init__.py"))
    has_main_py = os.path.exists(os.path.join(plugin_dir, "main.py"))

    if is_package and has_main_py:
        # 包式插件（Python AstrBot 生态惯例）：目录名.main，相对导入可用。
        # 父目录进 sys.path 供包内 import 使用。
        # 约定：一进程一插件，sys.path 注入不做清理（进程退出即回收）。
        sys.path.insert(0, os.path.dirname(plugin_dir))
        module = _load_package_plugin(plugin_dir, os.path.basename(plugin_dir))
    else:
        # 简单插件：main.py 作为顶层模块或 namespace 包子模块。
        # 目录/父目录进 sys.path，供相对导入（namespace 包）与子包发现。
        # 约定：一进程一插件，sys.path 注入不做清理（进程退出即回收）。
        sys.path.insert(0, os.path.dirname(plugin_dir))
        sys.path.insert(0, plugin_dir)
        if has_main_py:
            if is_package:
                module = _load_package_plugin(plugin_dir, os.path.basename(plugin_dir))
            else:
                # 无 __init__.py：namespace package 加载（对齐 Python AstrBot
                # 对 box 这类插件的支持：main.py 相对导入可用）。
                module = _load_namespace_package_plugin(
                    plugin_dir, os.path.basename(plugin_dir)
                )
        elif os.path.exists(os.path.join(plugin_dir, "__init__.py")):
            module = importlib.import_module(sanitize_module_name(os.path.basename(plugin_dir)))
        else:
            for name in ("main", "plugin", os.path.basename(plugin_dir)):
                if os.path.exists(os.path.join(plugin_dir, f"{name}.py")) or os.path.isdir(
                    os.path.join(plugin_dir, name)
                ):
                    module = importlib.import_module(sanitize_module_name(name))
                    break
            else:
                module = None
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

    # metadata.yaml 元数据优先（Python AstrBot 本体语义）
    _apply_yaml_metadata(plugin_dir, metadata)
    metadata.module = module
    metadata.root_dir_name = os.path.basename(plugin_dir)
    return metadata


def instantiate_plugin(metadata: StarMetadata, context: Context) -> None:
    """阶段 B：实例化 Star 并执行 initialize()。必须在宿主 Register 完成
    （HostService 身份绑定）后调用，插件 __init__/get_config 才能通过
    宿主 GetConfig 的身份校验。"""
    star_cls = metadata.star_cls_type
    if star_cls is None:
        raise ImportError(f"插件 {metadata.module.__name__} 的 Star 类未被识别")

    # 先设置身份，插件 __init__/get_config() 才能用注册名访问宿主配置
    context.plugin_name = metadata.name or metadata.root_dir_name
    context.plugin_id = metadata.plugin_id

    # 从宿主拉取插件配置
    try:
        config = context.get_config()
    except Exception as e:
        logger.warning(f"插件 {metadata.module.__name__} 配置拉取失败: {e}")
        config = None

    # 实例化：部分插件 __init__(self, context) 不收 config（ragecoop 等）。
    # 对齐 Python 本体 star_manager：先带 config 试，TypeError 回退无 config。
    try:
        inst = star_cls(context, config)
    except TypeError as e:
        # 仅当异常确为参数问题（消息含 config/argument 关键字）才回退为
        # 仅 context 实例化；__init__ 内部抛出的 TypeError（与参数无关）不应
        # 被误判为"不收 config"，否则会以错误状态重试。
        if (
            "config" not in str(e)
            and "positional argument" not in str(e)
            and "argument" not in str(e)
        ):
            raise
        logger.info(
            f"插件 {metadata.name or metadata.root_dir_name} 的 __init__ 不接受 "
            f"config 参数，回退为仅 context 实例化"
        )
        inst = star_cls(context)
    metadata.star_cls = inst

    # 注入 plugin_id（对齐 Python 本体 star_manager 的 setattr）
    plugin_id = metadata.plugin_id
    if hasattr(inst, "plugin_id") is False:
        try:
            setattr(star_cls, "plugin_id", plugin_id)
        except Exception as e:
            logger.warning(f"注入 plugin_id 失败（插件 {metadata.module.__name__}）: {e}")
    try:
        setattr(star_cls, "name", metadata.name or "")
    except Exception as e:
        logger.warning(f"注入 name 失败（插件 {metadata.module.__name__}）: {e}")

    if config is not None and not hasattr(inst, "config"):
        inst.config = config

    # 生命周期 initialize()
    init = getattr(inst, "initialize", None)
    if init is not None:
        loop.run_coro(init(), timeout=INIT_TIMEOUT)
        logger.info(f"插件 {metadata.module.__name__} initialize() 完成")


def load_plugin(plugin_dir: str, context: Context) -> StarMetadata | None:
    """import 插件并实例化其 Star 类（两阶段组合，见 load_plugin_import /
    instantiate_plugin）。失败抛异常。"""
    metadata = load_plugin_import(plugin_dir, context)
    if metadata is None:
        return None
    instantiate_plugin(metadata, context)
    return metadata


def terminate_plugin(metadata: StarMetadata) -> None:
    """调用插件的 terminate()（宿主卸载时）。"""
    inst = metadata.star_cls if metadata else None
    if inst is None:
        return
    # 取消插件登记的常驻任务（context.register_task）
    try:
        ctx = getattr(inst, "context", None)
        if ctx is not None and hasattr(ctx, "cancel_all_tasks"):
            ctx.cancel_all_tasks()
    except Exception as e:
        logger.warning(f"插件 {metadata.name} 任务清理失败: {e}")
    for name in ("terminate", "shutdown"):
        fn = getattr(inst, name, None)
        if fn is None:
            continue
        try:
            loop.run_coro(fn(), timeout=TERM_TIMEOUT)
        except Exception as e:
            logger.warning(f"插件 {metadata.name} {name}() 失败: {e}")
        break
