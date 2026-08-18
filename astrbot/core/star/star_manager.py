"""插件管理（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.star.star_manager.PluginManager` 中插件
（宿主管理插件等）最常用的四个操作：启用/禁用/安装/卸载插件。实际执行
都在 Go 宿主侧（SetPluginEnabled/InstallPlugin/UninstallPlugin RPC），
本模块只负责转发与错误包装。

同时提供 `StarInfo`：包装宿主 ListStars/GetStar 返回的插件元数据 dict，
插件侧按属性访问（plugin.name / plugin.author / plugin.desc /
plugin.activated / plugin.module_path / plugin.version）。
"""
from __future__ import annotations

import logging
from typing import Any

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

    async def install_plugin(self, repo: str) -> None:
        """安装插件（repo 为仓库地址）。"""
        try:
            ok = await self._bridge().install_plugin_async(repo)
        except Exception as e:
            logger.error(f"安装插件 {repo} 失败: {e}")
            raise Exception(f"安装插件 {repo} 失败: {e}") from e
        if not ok:
            raise Exception(f"安装插件 {repo} 失败。")

    async def uninstall_plugin(self, plugin_name: str) -> None:
        """卸载插件。"""
        try:
            ok = await self._bridge().uninstall_plugin_async(plugin_name)
        except Exception as e:
            logger.error(f"卸载插件 {plugin_name} 失败: {e}")
            raise Exception(f"卸载插件 {plugin_name} 失败: {e}") from e
        if not ok:
            raise Exception(f"卸载插件 {plugin_name} 失败。")

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
