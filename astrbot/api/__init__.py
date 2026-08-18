from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.provider.func_tool_manager import (
    FuncTool,
    FunctionToolManager,
    ToolSet,
)

# FunctionTool：Python 插件用 dataclass 子类化定义 LLM 工具（对齐 Python
# 原版 astrbot.core.agent.tool.FunctionTool 的常见用法）：
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
from collections.abc import AsyncGenerator
from dataclasses import dataclass as _dataclass
from dataclasses import field as _field
from typing import Any


@_dataclass
class FunctionTool:
    """LLM 函数工具基类（嵌入式 SDK 兼容层）。

    插件子类声明 name/description/parameters + async run(event, **kwargs)；
    宿主管线经 HandleTool 调用 run，返回值（str / MessageEventResult /
    mcp CallToolResult）会转换为文本反馈给模型。
    """

    name: str = ""
    description: str = ""
    parameters: dict = _field(default_factory=dict)
    handler: Any = None
    active: bool = True

    async def call(self, context, **kwargs):
        raise NotImplementedError(
            "FunctionTool.call() 必须由子类实现（或实现 async run(event, **kwargs)）"
        )


class BaseFunctionToolExecutor:
    """LLM 函数工具执行器基类（对齐 Python 本体
    astrbot.core.agent.tool_executor.BaseFunctionToolExecutor）。

    Go 宿主无自定义工具执行器管线，这里提供轻量默认实现：依次尝试
    调用 tool 的 handler / call() / run() 后，yield 工具结果（文本）。
    插件可继承本类并覆写 execute() 实现自己的工具调用逻辑。
    """

    @classmethod
    async def execute(
        cls,
        tool: FunctionTool,
        run_context: Any,
        **tool_args: Any,
    ) -> AsyncGenerator[Any, None]:
        """执行一次工具调用，yield 工具结果。

        Args:
            tool: 要执行的函数工具（FunctionTool 或其子类实例）
            run_context: 运行上下文（宿主传入，通常为 Context 包装）
            tool_args: 工具调用参数

        Yields:
            工具执行结果（str / MessageEventResult / 任意对象）
        """
        import inspect

        if tool.handler is not None and callable(tool.handler):
            result = tool.handler(**tool_args)
            if inspect.isawaitable(result):
                result = await result
            yield result
            return

        # 依次尝试 call() / run()：基类 call() 默认抛 NotImplementedError
        #（异步方法需 await 后才抛出），视为"未实现"并回退到下一个候选
        for caller_name in ("call", "run"):
            caller = getattr(tool, caller_name, None)
            if not callable(caller):
                continue
            try:
                result = caller(run_context, **tool_args)
                if inspect.isawaitable(result):
                    result = await result
            except NotImplementedError:
                continue
            yield result
            return

        raise NotImplementedError(
            "工具既无 handler，也未实现 call()/run()，无法执行"
        )


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


class _PluginContextLogger:
    """把 astrbot.api.logger 调用路由到调用方插件 logger。"""

    def __getattr__(self, item: str):
        import logging
        import sys

        module_name = sys._getframe(1).f_globals.get("__name__", "")
        return getattr(logging.getLogger(f"astrbot.plugin.{module_name}"), item)


logger = _PluginContextLogger()

html_renderer = None  # Go 宿主无 HTML 渲染

# sp：SharedPreferences 共享偏好存储（跨插件共享，作用域化），对齐 Python 本体
# astrbot.api.sp。数据持久化在宿主数据目录 shared_preferences.json。
from astrbot.core.utils.shared_preferences import sp  # noqa: E402

__all__ = [
    "AstrBotConfig",
    "BaseFunctionToolExecutor",
    "FuncTool",
    "FunctionTool",
    "FunctionToolManager",
    "ToolSet",
    "agent",
    "html_renderer",
    "llm_tool",
    "logger",
    "sp",
]
