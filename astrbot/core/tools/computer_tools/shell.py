"""computer_tools.shell（Go 宿主兼容运行时，对齐本体 computer_tools/shell.py）。

SDK 薄壳：Shell 执行工具类定义对齐本体，真实执行由宿主 sandbox 原生完成。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.core.agent.tool import FunctionTool


@dataclass
class ExecuteShellTool(FunctionTool):
    name: str = "astrbot_execute_shell"
    description: str = "Execute a command in the shell."
    parameters: dict = field(default_factory=dict)


@dataclass
class LocalExecuteShellTool(ExecuteShellTool):
    """本地受限 shell 执行（SDK 薄壳：继承 ExecuteShellTool 命名对齐）。"""


@dataclass
class ShellSessionTool(FunctionTool):
    name: str = "astrbot_shell_session"
    description: str = "Maintain a persistent shell session in the sandbox."
    parameters: dict = field(default_factory=dict)


__all__ = ["ExecuteShellTool", "LocalExecuteShellTool", "ShellSessionTool"]