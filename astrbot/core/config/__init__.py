from .astrbot_config import (
    ASTRBOT_CONFIG_PATH,
    AstrBotConfig,
    RateLimitStrategy,
)
from .default import DB_PATH, DEFAULT_CONFIG, DEFAULT_VALUE_MAP, VERSION

# __all__ 对齐本体 astrbot.core.config.__init__；包命名空间中额外保留
# ASTRBOT_CONFIG_PATH / RateLimitStrategy / DEFAULT_VALUE_MAP（本体经
# `from .astrbot_config import *` / default 导入同样可从包命名空间访问）。
__all__ = [
    "DB_PATH",
    "DEFAULT_CONFIG",
    "VERSION",
    "AstrBotConfig",
]
