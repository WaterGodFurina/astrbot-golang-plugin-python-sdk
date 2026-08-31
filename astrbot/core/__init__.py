"""AstrBot Core 包（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core` 的对外常量与工具：插件常以
`from astrbot.core import DEMO_MODE, logger` 方式使用。
"""
import logging

# 演示模式（Go 宿主下恒为 False：宿主本身没有演示模式限制）
DEMO_MODE = False

# 顶层日志器：与 astrbot 包共享（插件侧 logger 由 astrbot.api.logger
# 按调用方模块路由，这里仅提供别名，避免插件 import 失败）
logger = logging.getLogger("astrbot")

# 全局异常（对齐本体 astrbot.core.exceptions / pip_installer 导出）
from astrbot.core.exceptions import (  # noqa: E402
    AstrBotError,
    DependencyConflictError,
    EmptyModelOutputError,
    KnowledgeBaseUploadError,
    ProviderNotFoundError,
)

# html_renderer：渲染实例（对齐本体 `from astrbot.core import html_renderer`；
# 独立模块实现，避免与 astrbot.api 产生循环导入，astrbot.api re-export 同一实例）
from astrbot.core.utils.html_renderer import html_renderer  # noqa: E402

# sp：共享偏好存储（对齐本体 `from astrbot.core import sp`）
from astrbot.core.utils.shared_preferences import sp  # noqa: E402

# AstrBotConfig：配置类（对齐本体 `from astrbot.core import AstrBotConfig`；
# 插件常据此构造/访问插件配置）
from astrbot.core.config import AstrBotConfig  # noqa: E402

# FileTokenService / file_token_service：文件令牌服务（对齐本体导出）
from astrbot.core.file_token_service import (  # noqa: E402
    FileTokenService,
    file_token_service,
)

# LogManager / LogBroker：插件日志管理器与日志代理（对齐本体
# `from astrbot.core import LogManager, LogBroker`）
from astrbot.core.log import LogManager, LogBroker  # noqa: E402

# DB_PATH：数据库文件路径常量（对齐本体 `from astrbot.core import DB_PATH`）
from astrbot.core.config.default import DB_PATH  # noqa: E402

# find_missing_requirements(_or_raise)：依赖缺失检查（对齐本体导出，签名一致）
from astrbot.core.utils.requirements_utils import (  # noqa: E402
    find_missing_requirements,
    find_missing_requirements_or_raise,
)

# SharedPreferences：共享偏好存储类（对齐本体导出）
from astrbot.core.utils.shared_preferences import SharedPreferences  # noqa: E402

# HtmlRenderer：HTML 渲染器类（对齐本体导出）
from astrbot.core.utils.html_renderer import HtmlRenderer  # noqa: E402

# RequirementsPrecheckFailed：插件依赖预检异常（对齐本体导出）
from astrbot.core.utils.requirements_utils import RequirementsPrecheckFailed  # noqa: E402

# get_astrbot_data_path：数据目录解析（对齐本体导出）
from astrbot.core.utils.astrbot_path import get_astrbot_data_path  # noqa: E402
