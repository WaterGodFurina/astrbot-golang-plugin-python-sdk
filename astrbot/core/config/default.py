"""默认配置常量（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.config.default` 的常用常量。
"""
import os

from astrbot import __version__
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

VERSION = __version__

DB_PATH = os.path.join(get_astrbot_data_path(), "data_v4.db")

# 支持统一 Webhook 回调的平台（对齐本体 config.default.WEBHOOK_SUPPORTED_PLATFORMS）。
WEBHOOK_SUPPORTED_PLATFORMS = [
    "qq_official_webhook",
    "weixin_official_account",
    "wecom",
    "wecom_ai_bot",
    "slack",
    "lark",
    "line",
]
