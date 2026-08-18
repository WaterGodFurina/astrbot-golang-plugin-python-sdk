"""AstrBot 配置管理器（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.astrbot_config_mgr.AstrBotConfigManager`。
SDK 降级实现：get_config / get_config_async 返回 AstrBotConfig 占位，
get_config_path 返回数据目录下 config.json 路径。
"""
from __future__ import annotations

import os

from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


class AstrBotConfigManager:
    """配置文件管理器（非 webui）（SDK 降级）。"""

    def get_config_path(self) -> str:
        """返回主配置文件路径（data/config.json）。"""
        return os.path.join(get_astrbot_data_path(), "config.json")

    def get_config(self, umo: str | None = None) -> AstrBotConfig:
        """获取 AstrBot 配置（SDK 降级：返回 AstrBotConfig 占位）。"""
        return AstrBotConfig()

    async def get_config_async(self, umo: str | None = None) -> AstrBotConfig:
        """异步获取 AstrBot 配置（SDK 降级：返回 AstrBotConfig 占位）。"""
        return AstrBotConfig()

    def get_conf(self, umo: str | None = None) -> AstrBotConfig:
        """按 umo 获取配置（对齐本体 get_conf 语义，SDK 降级）。"""
        return self.get_config(umo)
