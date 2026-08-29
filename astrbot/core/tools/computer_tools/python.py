"""computer_tools.python（Go 宿主兼容运行时，对齐本体 computer_tools/python.py）。

SDK 薄壳：Python 执行工具类定义对齐本体，真实执行由宿主 sandbox 原生完成。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.core.agent.tool import FunctionTool


@dataclass
class PythonTool(FunctionTool):
    name: str = "astrbot_execute_python"
    description: str = "Run Python code in the sandbox (host-sandbox isolated)."
    parameters: dict = field(default_factory=dict)


@dataclass
class LocalPythonTool(FunctionTool):
    name: str = "astrbot_execute_ipython"
    description: str = "Run Python code locally in an IPython kernel."
    parameters: dict = field(default_factory=dict)


__all__ = ["LocalPythonTool", "PythonTool"]