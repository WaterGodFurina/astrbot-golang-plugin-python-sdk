"""computer_tools.cua（Go 宿主兼容运行时，对齐本体 computer_tools/cua.py）。

SDK 薄壳：工具类定义与 name/description 对齐本体，call 由宿主 sandbox 原生执行。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from astrbot.core.agent.tool import FunctionTool


@dataclass
class CuaScreenshotTool(FunctionTool):
    """CUA 沙盒截图工具（宿主 sandbox 原生执行）。"""

    name: str = "astrbot_cua_screenshot"
    description: str = "Capture the current screen of the CUA sandbox desktop."
    parameters: dict = field(default_factory=dict)


@dataclass
class CuaMouseClickTool(FunctionTool):
    """CUA 沙盒鼠标点击工具（宿主 sandbox 原生执行）。"""

    name: str = "astrbot_cua_mouse_click"
    description: str = "Click a coordinate in the CUA sandbox desktop."
    parameters: dict = field(default_factory=dict)


@dataclass
class CuaKeyboardTypeTool(FunctionTool):
    """CUA 沙盒键盘输入工具（宿主 sandbox 原生执行）。"""

    name: str = "astrbot_cua_keyboard_type"
    description: str = "Type text into the CUA sandbox desktop."
    parameters: dict = field(default_factory=dict)


__all__ = ["CuaKeyboardTypeTool", "CuaMouseClickTool", "CuaScreenshotTool"]