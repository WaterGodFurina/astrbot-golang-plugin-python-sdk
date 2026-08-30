"""computer_tools.python（Go 宿主兼容运行时，对齐本体 computer_tools/python.py）。

SDK 薄壳：Python 执行工具类的 name / description / parameters（schema）与
本体一致，真实执行由宿主 sandbox 原生完成。
"""
from __future__ import annotations

import platform
from dataclasses import dataclass, field

from astrbot.core.agent.tool import FunctionTool

_OS_NAME = platform.system()

_param_schema: dict = {
    "type": "object",
    "properties": {
        "code": {
            "type": "string",
            "description": "The Python code to execute.",
        },
        "silent": {
            "type": "boolean",
            "description": "Whether to suppress the output of the code execution.",
            "default": False,
        },
        "timeout": {
            "type": "integer",
            "description": "Optional timeout in seconds for code execution.",
            "default": 30,
        },
    },
    "required": ["code"],
}


@dataclass
class PythonTool(FunctionTool):
    name: str = "astrbot_execute_ipython"
    description: str = f"Run codes in an IPython shell. Current OS: {_OS_NAME}."
    parameters: dict = field(default_factory=lambda: dict(_param_schema))


@dataclass
class LocalPythonTool(FunctionTool):
    name: str = "astrbot_execute_python"
    description: str = (
        f"Execute codes in a Python environment. Current OS: {_OS_NAME}. "
        "Use system-compatible commands."
    )
    parameters: dict = field(default_factory=lambda: dict(_param_schema))


__all__ = ["LocalPythonTool", "PythonTool"]