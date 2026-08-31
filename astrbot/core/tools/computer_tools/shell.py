"""computer_tools.shell（Go 宿主兼容运行时，对齐本体 computer_tools/shell.py）。

SDK 薄壳：shell 工具类的 name / description / parameters（schema）与本体一致
并经 ``builtin_tool`` 注册（LocalExecuteShellTool 与本体一致不单独注册），
真实执行由宿主 sandbox 原生完成。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.core.agent.tool import FunctionTool
from astrbot.core.tools.registry import builtin_tool

# 对齐本体 shell.py:25-30 的装饰器 config。
_COMPUTER_RUNTIME_TOOL_CONFIG = {
    "provider_settings.computer_use_runtime": ("local", "sandbox"),
}
_LOCAL_RUNTIME_TOOL_CONFIG = {
    "provider_settings.computer_use_runtime": "local",
}


@builtin_tool(config=_COMPUTER_RUNTIME_TOOL_CONFIG)
@dataclass
class ExecuteShellTool(FunctionTool):
    name: str = "astrbot_execute_shell"
    description: str = "Execute a command in the shell."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute in the current runtime shell (for example, PowerShell on Windows). Equal to 'cd {working_dir} && {your_command}'.",
                },
                "background": {
                    "type": "boolean",
                    "description": "Run the command in the background. Use the file read tool to read the output later. For long running commands, using this option.",
                    "default": False,
                },
                "timeout": {
                    "type": "integer",
                    "description": "Optional timeout in seconds for the command execution.",
                    "default": 300,
                },
                "env": {
                    "type": "object",
                    "description": "Optional environment variables to set.",
                    "additionalProperties": {"type": "string"},
                    "default": {},
                },
            },
            "required": ["command"],
        }
    )


@dataclass
class LocalExecuteShellTool(ExecuteShellTool):
    name: str = "astrbot_execute_shell"
    description: str = (
        "Execute a command in the shell. If it is still running after yield_time_ms, "
        "the tool returns a managed shell session ID."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute in the current workspace.",
                },
                "yield_time_ms": {
                    "type": "integer",
                    "description": "Maximum time to wait for completion before returning a managed shell session. This does not stop the process.",
                    "default": 10000,
                    "minimum": 0,
                    "maximum": 30000,
                },
                "timeout": {
                    "type": "integer",
                    "description": "Optional hard process lifetime in seconds. Omit it to allow the managed session to keep running.",
                    "minimum": 1,
                },
                "env": {
                    "type": "object",
                    "description": "Optional environment variables to set.",
                    "additionalProperties": {"type": "string"},
                    "default": {},
                },
            },
            "required": ["command"],
        }
    )


@builtin_tool(config=_LOCAL_RUNTIME_TOOL_CONFIG)
@dataclass
class ShellSessionTool(FunctionTool):
    name: str = "astrbot_shell_session"
    description: str = (
        "List, poll, write raw text or complete lines to, interrupt, or terminate managed shell sessions. "
        "Sessions are isolated to the current conversation and sender. "
        "Administrators can manage all sessions in the conversation."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "poll", "write", "write_line", "interrupt", "terminate"],
                    "description": "Session operation to perform.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Required for every action except list.",
                },
                "chars": {
                    "type": "string",
                    "description": "Text sent verbatim by write. For write_line, provide one line without a line ending; a real LF is appended automatically.",
                    "default": "",
                },
                "cursor": {
                    "type": "integer",
                    "description": "Optional byte cursor for poll. Omit to continue from the last returned output.",
                    "minimum": 0,
                },
                "yield_time_ms": {
                    "type": "integer",
                    "description": "Maximum time poll or interrupt waits for output or exit.",
                    "default": 5000,
                    "minimum": 0,
                    "maximum": 30000,
                },
                "max_output_chars": {
                    "type": "integer",
                    "description": "Maximum output bytes returned by poll, interrupt, or terminate.",
                    "default": 10000,
                    "minimum": 1,
                    "maximum": 100000,
                },
            },
            "required": ["action"],
        }
    )


__all__ = [
    "ExecuteShellTool",
    "LocalExecuteShellTool",
    "ShellSessionTool",
]