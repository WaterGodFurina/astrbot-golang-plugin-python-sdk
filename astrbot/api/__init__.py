import logging

from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.provider.func_tool_manager import (
    FuncTool,
    FunctionToolManager,
)
from astrbot.core.agent.tool import FunctionTool, ToolSchema, ToolSet

# FunctionTool / ToolSet / ToolSchema：实现本体在 astrbot.core.agent.tool
# （普通 dataclass，无需 pydantic）。Python 插件用 dataclass 子类化定义 LLM
# 工具（对齐 Python 原版 astrbot.core.agent.tool.FunctionTool 的常见用法）：
#
#     from astrbot.api import FunctionTool, logger
#     from dataclasses import dataclass, field
#
#     @dataclass
#     class MyTool(FunctionTool):
#         name: str = "my_tool"
#         description: str = "..."
#         parameters: dict = field(default_factory=lambda: {...})
#
#         async def run(self, event, **kwargs) -> str: ...
#
# 注意用普通 dataclass（非 pydantic）装饰，保证任意版本的插件子类都能继承。
# BaseFunctionToolExecutor：与 core 侧同一对象（对齐本体
# `from astrbot.core.agent.tool_executor import BaseFunctionToolExecutor`）。
from astrbot.core.agent.tool_executor import BaseFunctionToolExecutor

llm_tool = __import__(
    "astrbot.core.star.register.star_handler", fromlist=["register_llm_tool"]
).register_llm_tool

agent = __import__(
    "astrbot.core.star.register.star_handler", fromlist=["register_agent"]
).register_agent


def _plugin_logger():
    import logging
    import sys

    module_name = sys._getframe(1).f_globals.get("__name__", "")
    return logging.getLogger(f"astrbot.plugin.{module_name}")


# 按 module_name 缓存插件 logger：日志每次调用都走 __getattr__，
# 避免反复 sys._getframe(1) 查找调用方模块（对齐参考的缓存机制）。
_logger_cache: dict[str, logging.Logger] = {}


class _PluginContextLogger:
    """把 astrbot.api.logger 调用路由到调用方插件 logger。"""

    def __getattr__(self, item: str):
        import logging
        import sys

        module_name = sys._getframe(1).f_globals.get("__name__", "")
        logger = _logger_cache.get(module_name)
        if logger is None:
            logger = logging.getLogger(f"astrbot.plugin.{module_name}")
            _logger_cache[module_name] = logger
        return getattr(logger, item)


logger = _PluginContextLogger()


# HtmlRenderer / html_renderer：re-export core 侧同一对象（对齐本体
# `from astrbot.core import html_renderer`），避免同名不同义的两套实现；
# 渲染走宿主桥（text_to_image_async / html_render_async）。
from astrbot.core.utils.html_renderer import HtmlRenderer, html_renderer

# sp：SharedPreferences 共享偏好存储（跨插件共享，作用域化），对齐 Python 本体
# astrbot.api.sp。数据持久化在宿主数据目录 shared_preferences.json。
from astrbot.core.utils.shared_preferences import sp  # noqa: E402

__all__ = [
    "AstrBotConfig",
    "BaseFunctionToolExecutor",
    "FuncTool",
    "FunctionTool",
    "FunctionToolManager",
    "HtmlRenderer",
    "ToolSchema",
    "ToolSet",
    "agent",
    "html_renderer",
    "llm_tool",
    "logger",
    "sp",
]
