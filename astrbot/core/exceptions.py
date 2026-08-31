"""AstrBot 全局异常（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.exceptions`（/ `astrbot.core.utils.pip_installer`
中导出的 DependencyConflictError），插件常用的
`from astrbot.core.exceptions import ...` / `from astrbot.core import ...`
路径均指向这里。

本体层级：AstrBotError 为基类，ProviderNotFoundError / EmptyModelOutputError /
KnowledgeBaseUploadError 均继承之；插件 ``except AstrBotError`` 需能捕获全部。
"""


class AstrBotError(Exception):
    """AstrBot 所有错误的基类（对齐本体）。"""


class ProviderNotFoundError(AstrBotError):
    """当按 ID 找不到 Provider 时抛出（本体继承 AstrBotError）。"""


class EmptyModelOutputError(AstrBotError):
    """当模型响应没有可用的助手输出时抛出（对齐本体）。"""


class KnowledgeBaseUploadError(AstrBotError):
    """知识库上传失败时抛出，携带面向用户的消息（对齐本体）。"""

    def __init__(
        self,
        *,
        stage: str,
        user_message: str,
        details: dict | None = None,
    ) -> None:
        super().__init__(user_message)
        self.stage = stage
        self.user_message = user_message
        self.details = details or {}

    def __str__(self) -> str:
        return self.user_message


class DependencyConflictError(Exception):
    """当插件依赖与当前环境已安装依赖冲突时抛出（pip 安装失败时包装）。"""
