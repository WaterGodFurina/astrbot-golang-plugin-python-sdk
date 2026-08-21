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
from collections.abc import AsyncGenerator
from dataclasses import dataclass as _dataclass  # noqa: F401  （插件子类化模式提示）
from dataclasses import field as _field  # noqa: F401  （插件子类化模式提示）
from typing import Any

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
            result = tool.handler(run_context, **tool_args)
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


class HtmlRenderer:
    """HTML 文转图渲染器占位实现（Go 宿主无独立 HTML 模板引擎）。

    render_t2i 走宿主桥 HostBridge.text_to_image_async（宿主渲染文本为
    图片，返回 base64 PNG）解码为 PNG 字节；return_url=True 时返回
    data:image/png;base64,... 前缀的 URL，否则返回 bytes。渲染失败返回
    None。render_custom_template 走宿主桥 HostBridge.html_render_async
    渲染自定义模板（tmpl + data JSON），同样失败返回 None。
    """

    async def render_t2i(self, text: str, return_url: bool = True):
        """将文本渲染为图片，返回 data URL（默认）或 PNG 字节。"""
        import base64

        from astrbot._bridge.host import get_bridge

        try:
            png = await get_bridge().text_to_image_async(text)
        except Exception:
            return None
        if not return_url:
            return png
        return "data:image/png;base64," + base64.b64encode(png).decode()

    async def render_custom_template(self, tmpl: str, data: dict, return_url: bool = True):
        """自定义模板渲染：经宿主 HtmlRender 桥渲染。

        模板内容 tmpl 与渲染数据 data（序列化为 JSON）一并交给宿主，返回
        渲染出的 PNG 图片（return_url=True 时返回 data URL，否则返回 PNG
        字节）；渲染失败返回 None 并告警。
        """
        import base64
        import json

        from astrbot._bridge.host import get_bridge

        try:
            png = await get_bridge().html_render_async(
                template=tmpl,
                data=json.dumps(data, ensure_ascii=False),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"自定义模板渲染失败: {e}")
            return None
        if not return_url:
            return png
        return "data:image/png;base64," + base64.b64encode(png).decode()

    async def initialize(self) -> None:
        """初始化：无宿主资源需要预热，no-op。"""


html_renderer = HtmlRenderer()  # 对齐原版 astrbot.core.html_renderer 的占位实例

# sp：SharedPreferences 共享偏好存储（跨插件共享，作用域化），对齐 Python 本体
# astrbot.api.sp。数据持久化在宿主数据目录 shared_preferences.json。
from astrbot.core.utils.shared_preferences import sp  # noqa: E402

__all__ = [
    "AstrBotConfig",
    "BaseFunctionToolExecutor",
    "FuncTool",
    "FunctionTool",
    "FunctionToolManager",
    "ToolSchema",
    "ToolSet",
    "agent",
    "html_renderer",
    "llm_tool",
    "logger",
    "sp",
]
