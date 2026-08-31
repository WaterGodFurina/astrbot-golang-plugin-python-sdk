"""computer_tools.cua（Go 宿主兼容运行时，对齐本体 computer_tools/cua.py）。

SDK 薄壳：工具类 name / description / parameters（schema）与本体一致并经
``builtin_tool`` 注册；call 由宿主 sandbox 的 CUA GUI 能力原生执行。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.core.agent.tool import FunctionTool
from astrbot.core.tools.registry import builtin_tool

# 对齐本体 cua.py:22-25 的装饰器 config。
_CUA_TOOL_CONFIG = {
    "provider_settings.computer_use_runtime": "sandbox",
    "provider_settings.sandbox.booter": "cua",
}


@builtin_tool(config=_CUA_TOOL_CONFIG)
@dataclass
class CuaScreenshotTool(FunctionTool):
    """CUA 沙盒截图工具（宿主 sandbox 原生执行）。"""

    name: str = "astrbot_cua_screenshot"
    description: str = (
        "Capture a screenshot from the CUA sandbox and optionally send it to the user."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "send_to_user": {
                    "type": "boolean",
                    "description": "Whether to send the screenshot image to the current conversation.",
                    "default": True,
                },
                "return_image_to_llm": {
                    "type": "boolean",
                    "description": "Whether to include the screenshot image content in the tool result for model inspection.",
                    "default": True,
                },
            },
        }
    )


@builtin_tool(config=_CUA_TOOL_CONFIG)
@dataclass
class CuaMouseClickTool(FunctionTool):
    """CUA 沙盒鼠标点击工具（宿主 sandbox 原生执行）。"""

    name: str = "astrbot_cua_mouse_click"
    description: str = "Click a coordinate in the CUA sandbox desktop."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate."},
                "y": {"type": "integer", "description": "Y coordinate."},
                "button": {
                    "type": "string",
                    "description": "Mouse button, usually left, right, or middle.",
                    "default": "left",
                },
            },
            "required": ["x", "y"],
        }
    )


@builtin_tool(config=_CUA_TOOL_CONFIG)
@dataclass
class CuaKeyboardTypeTool(FunctionTool):
    """CUA 沙盒键盘输入工具（宿主 sandbox 原生执行）。"""

    name: str = "astrbot_cua_keyboard_type"
    description: str = "Type text into the CUA sandbox desktop."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type."},
            },
            "required": ["text"],
        }
    )


__all__ = ["CuaKeyboardTypeTool", "CuaMouseClickTool", "CuaScreenshotTool"]