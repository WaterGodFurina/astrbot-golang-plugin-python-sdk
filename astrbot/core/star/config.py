"""旧式插件配置 API 兼容（参考 Python astrbot.core.star.config）。

对齐原版模块顶部 import（json / os / get_astrbot_data_path），
实现上委托 AstrBotConfig 的静态方法（逻辑不变）。
"""
import json
import os

from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path


def load_config(namespace: str) -> dict | bool:
    """从配置文件中加载配置。"""
    return AstrBotConfig.load_config(namespace)


def put_config(namespace: str, name: str, key: str, value, description: str) -> None:
    """将配置项写入以 namespace 为名字的配置文件。"""
    return AstrBotConfig.put_config(namespace, name, key, value, description)


def update_config(namespace: str, key: str, value) -> None:
    """更新配置文件中的配置项。"""
    return AstrBotConfig.update_config(namespace, key, value)
