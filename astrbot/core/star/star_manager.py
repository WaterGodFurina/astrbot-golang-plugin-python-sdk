"""插件管理（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.star.star_manager.PluginManager` 中插件
（宿主管理插件等）最常用的四个操作：启用/禁用/安装/卸载插件。实际执行
都在 Go 宿主侧（SetPluginEnabled/InstallPlugin/UninstallPlugin RPC），
本模块只负责转发与错误包装。

同时提供 `StarInfo`：包装宿主 GetPluginRegistry/GetStar 返回的插件元数据 dict，
插件侧按属性访问（plugin.name / plugin.author / plugin.desc /
plugin.activated / plugin.module_path / plugin.version）。
"""
from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from astrbot.core import DependencyConflictError, logger
from astrbot.core.config.default import VERSION
from astrbot.core.platform.register import unregister_platform_adapters_by_module
from astrbot.core.provider.func_tool_manager import llm_tools
from astrbot.core.star.command_management import sync_command_configs
from astrbot.core.star.error_messages import format_plugin_error
from astrbot.core.star.filter.permission import PermissionType, PermissionTypeFilter
from astrbot.core.star.star import StarMetadata  # noqa: F401  re-export（对齐本体 star_manager 路径可 import）
from astrbot.core.star.updater import PLUGIN_METADATA_FILENAMES, _PluginUpdater
from astrbot.core.utils.astrbot_path import (
    get_astrbot_config_path,
    get_astrbot_plugin_path,
    get_astrbot_temp_path,
)
from astrbot.core.utils.io import remove_dir
from astrbot.core.utils.metrics import Metric
from astrbot.core.utils.requirements_utils import (
    MissingRequirementsPlan,
    plan_missing_requirements_install,
)
from astrbot.core.utils.shared_preferences import sp

# ── 模块级常量与符号（对齐原版 star_manager.py 顶部定义）──────────────────
PLUGIN_TOOL_STATE_MIGRATION_KEY = "inactivated_llm_tools_plugin_state_migrated_v1"
"""旧版插件工具失效状态迁移标记键（对齐原版值）。"""


class PluginVersionUnsupportedError(Exception):
    """当插件 astrbot_version 不受当前 AstrBot 支持时抛出。"""


class PluginDependencyInstallError(Exception):
    """当插件依赖安装失败时抛出。"""

    def __init__(
        self,
        *,
        plugin_label: str,
        requirements_path: str,
        error: Exception,
    ) -> None:
        message = f"Failed to install dependencies for plugin {plugin_label}: {error!s}"
        super().__init__(message)
        self.plugin_label = plugin_label
        self.requirements_path = requirements_path
        self.error = error


class ImportDependencyRecoveryMode(Enum):
    """插件依赖导入失败后的恢复模式（对齐原版取值）。"""

    DISABLED = auto()
    PRELOAD_AND_RECOVER = auto()
    RECOVER_ON_FAILURE = auto()
    REINSTALL_ON_FAILURE = auto()


@dataclass(frozen=True)
class ImportDependencyRecoveryState:
    """依赖导入恢复状态（对齐原版 frozen dataclass）。"""

    mode: ImportDependencyRecoveryMode
    install_plan: MissingRequirementsPlan | None = None


@contextlib.contextmanager
def _temporary_filtered_requirements_file(
    *,
    install_lines: tuple[str, ...],
):
    """在临时目录创建仅含待安装依赖行的临时 requirements 文件。"""
    filtered_requirements_path: str | None = None
    temp_dir = get_astrbot_temp_path()
    try:
        os.makedirs(temp_dir, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix="_plugin_requirements.txt",
            delete=False,
            dir=temp_dir,
            encoding="utf-8",
        ) as filtered_requirements_file:
            filtered_requirements_file.write("\n".join(install_lines) + "\n")
            filtered_requirements_path = filtered_requirements_file.name
        yield filtered_requirements_path
    finally:
        if filtered_requirements_path and os.path.exists(filtered_requirements_path):
            try:
                os.remove(filtered_requirements_path)
            except OSError as exc:
                logger.warning(
                    "Failed to remove the temporary plugin requirements file: %s "
                    "(path: %s)",
                    exc,
                    filtered_requirements_path,
                )


async def _install_requirements_with_precheck(
    *,
    plugin_label: str,
    requirements_path: str,
) -> None:
    """安装插件依赖（SDK 降级：预检查缺失项后打印日志，实际安装由宿主完成）。"""
    install_plan = plan_missing_requirements_install(requirements_path)

    if install_plan is None:
        logger.info(
            f"Installing dependencies for plugin {plugin_label}; the missing-"
            "dependency precheck could not safely reduce the requirements, so "
            "the complete requirements file will be installed: "
            f"{requirements_path}"
        )
        return

    if not install_plan.missing_names:
        logger.info(
            f"Dependencies for plugin {plugin_label} are already satisfied; "
            "skipping installation."
        )
        return

    logger.info(
        f"Plugin {plugin_label} has missing dependencies; installing them from "
        "requirements.txt: "
        f"{requirements_path} -> {sorted(install_plan.missing_names)}"
    )


logger = logging.getLogger("astrbot")


class StarInfo:
    """包装宿主返回的插件元数据 dict，提供属性访问。

    宿主字段约定：name / author / desc / version / module_path / activated / repo。
    """

    def __init__(self, data: dict | None = None) -> None:
        self._data: dict = data or {}
        self.name: str = str(self._data.get("name") or "")
        self.author: str = str(self._data.get("author") or "")
        self.desc: str = str(self._data.get("desc") or "")
        self.version: str = str(self._data.get("version") or "")
        self.module_path: str = str(self._data.get("module_path") or "")
        self.activated: bool = bool(self._data.get("activated", True))
        self.repo: str = str(self._data.get("repo") or "")

    def __getitem__(self, key: str) -> Any:
        return self._data.get(key)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def to_dict(self) -> dict:
        return dict(self._data)

    def __repr__(self) -> str:
        return f"StarInfo(name={self.name!r}, version={self.version!r}, activated={self.activated})"


class PluginManager:
    """插件管理：启停/安装/卸载（经宿主桥转发）。"""

    def __init__(self, context: Any | None = None, bridge: Any | None = None) -> None:
        self.context = context
        # bridge 可以是 HostBridge 实例，也可以是返回 HostBridge 的
        # 可调用对象（Context 传入 self._bridge 保持单桥来源一致）
        self._bridge_getter: Any = bridge
        # 对齐本体 star_manager.py:193-199：PluginManager 构造时把 Context
        # 注入 StarTools（模块级共享），否则 StarTools.send_message /
        # register_llm_tool / activate_llm_tool 等在生产中恒抛
        # "StarTools not initialized"（get_data_dir 之外的 StarTools 方法
        # 全部不可用）。
        if context is not None:
            from astrbot.core.star.star_tools import StarTools

            StarTools.initialize(context)

    def _bridge(self):
        if self._bridge_getter is None:
            if self.context is not None:
                bridge = getattr(self.context, "_bridge", None)
                if callable(bridge):
                    return bridge()
            raise RuntimeError("宿主桥未就绪（PluginManager 未绑定宿主）")
        if callable(self._bridge_getter):
            return self._bridge_getter()
        return self._bridge_getter

    async def turn_off_plugin(self, plugin_name: str) -> None:
        """禁用插件（对齐本体：插件不存在时抛异常）。"""
        plugin = await self._get_registered_star(plugin_name)
        if plugin is None:
            raise Exception("插件不存在。")
        try:
            ok = await self._bridge().set_plugin_enabled_async(plugin_name, False)
        except Exception as e:
            logger.error(f"禁用插件 {plugin_name} 失败: {e}")
            raise Exception(f"禁用插件 {plugin_name} 失败: {e}") from e
        if not ok:
            raise Exception(f"禁用插件 {plugin_name} 失败。")
        logger.info(f"插件 {plugin_name} 已禁用。")

    async def turn_on_plugin(self, plugin_name: str) -> None:
        """启用插件（对齐本体：插件不存在时抛异常）。"""
        plugin = await self._get_registered_star(plugin_name)
        if plugin is None:
            raise Exception(f"插件 {plugin_name} 不存在。")
        try:
            ok = await self._bridge().set_plugin_enabled_async(plugin_name, True)
        except Exception as e:
            logger.error(f"启用插件 {plugin_name} 失败: {e}")
            raise Exception(f"启用插件 {plugin_name} 失败: {e}") from e
        if not ok:
            raise Exception(f"启用插件 {plugin_name} 失败。")
        logger.info(f"插件 {plugin_name} 已启用。")

    async def install_plugin(
        self,
        repo_url: str,
        proxy: str = "",
        ignore_version_check: bool = False,
        download_url: str = "",
    ) -> None:
        """安装插件（对齐本体签名 install_plugin(repo_url, proxy, ignore_version_check, download_url)）。

        Go 宿主原生执行安装/依赖安装/编译（internal/plugin 安装链路），
        SDK 侧仅转发仓库地址；其余参数按本体签名保留（宿主忽略）。
        """
        try:
            ok = await self._bridge().install_plugin_async(repo_url)
        except Exception as e:
            logger.error(f"安装插件 {repo_url} 失败: {e}")
            raise Exception(f"安装插件 {repo_url} 失败: {e}") from e
        if not ok:
            raise Exception(f"安装插件 {repo_url} 失败。")

    async def uninstall_plugin(
        self,
        plugin_name: str,
        delete_config: bool = False,
        delete_data: bool = False,
    ) -> None:
        """卸载插件（对齐本体签名 uninstall_plugin(plugin_name, delete_config, delete_data)）。

        配置/数据清理由宿主按 delete_config/delete_data 处理；bridge 未
        提供细分参数时按原转发语义仅卸载插件本体。
        """
        try:
            ok = await self._bridge().uninstall_plugin_async(plugin_name)
        except Exception as e:
            logger.error(f"卸载插件 {plugin_name} 失败: {e}")
            raise Exception(f"卸载插件 {plugin_name} 失败: {e}") from e
        if not ok:
            raise Exception(f"卸载插件 {plugin_name} 失败。")

    async def install_plugin_from_file(
        self, zip_file_path: str, ignore_version_check: bool = False
    ) -> None:
        """从本地 zip 安装插件（对齐本体签名）。

        Go 宿主无插件子进程侧的 zip 安装 RPC（zip 安装由宿主 WebUI/
        dashboard 安装 API 原生处理），SDK 侧保留签名并显式降级。
        """
        raise RuntimeError(
            "install_plugin_from_file 由宿主安装链路原生处理"
            "（WebUI 插件上传/安装 API），插件子进程侧不支持该操作。"
        )

    async def update_plugin(
        self,
        plugin_name: str,
        proxy="",
        download_url: str = "",
        repo_url: str = "",
    ) -> None:
        """更新插件（对齐本体签名 update_plugin(plugin_name, proxy, download_url, repo_url)）。

        更新流程由宿主原生执行；SDK 侧按本体签名保留参数并转发插件名。
        """
        try:
            ok = await self._bridge().install_plugin_async(repo_url or plugin_name)
        except Exception as e:
            logger.error(f"更新插件 {plugin_name} 失败: {e}")
            raise Exception(f"更新插件 {plugin_name} 失败: {e}") from e
        if not ok:
            raise Exception(f"更新插件 {plugin_name} 失败。")

    async def _get_registered_star(self, plugin_name: str) -> StarInfo | None:
        try:
            data = await self._bridge().get_star_async(plugin_name)
        except Exception as e:
            logger.warning(f"get_star({plugin_name}) 失败: {e}")
            return None
        if not isinstance(data, dict) or not data:
            return None
        return StarInfo(data)


# 别名：本体中 Context._star_manager 即为 PluginManager 实例
StarManager = PluginManager
