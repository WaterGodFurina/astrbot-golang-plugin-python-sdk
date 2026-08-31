"""computer_tools.shipyard_neo.browser（Go 宿主兼容运行时，对齐本体）。

SDK 薄壳：浏览器编排工具类定义（name / description / parameters schema）
与本体一致并经 ``builtin_tool`` 注册，真实执行由宿主 shipyard-neo sandbox
完成（internal/pipeline/computer_tools.go 的 browser/shipyard 分支）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.core.agent.tool import FunctionTool
from astrbot.core.tools.registry import builtin_tool

# 对齐本体 browser.py:13-16 的装饰器 config。
_SHIPYARD_NEO_TOOL_CONFIG = {
    "provider_settings.computer_use_runtime": "sandbox",
    "provider_settings.sandbox.booter": "shipyard_neo",
}


@builtin_tool(config=_SHIPYARD_NEO_TOOL_CONFIG)
@dataclass
class BrowserExecTool(FunctionTool):
    """单条浏览器自动化命令（宿主 shipyard-neo 原生执行）。"""

    name: str = "astrbot_execute_browser"
    description: str = "Execute one browser automation command in the sandbox."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "Browser command to execute."},
                "timeout": {"type": "integer", "default": 30},
                "description": {
                    "type": "string",
                    "description": "Optional execution description.",
                },
                "tags": {"type": "string", "description": "Optional tags."},
                "learn": {
                    "type": "boolean",
                    "description": "Whether to mark execution as learn evidence.",
                    "default": False,
                },
                "include_trace": {
                    "type": "boolean",
                    "description": "Whether to include trace_ref in response.",
                    "default": False,
                },
            },
            "required": ["cmd"],
        }
    )


@builtin_tool(config=_SHIPYARD_NEO_TOOL_CONFIG)
@dataclass
class BrowserBatchExecTool(FunctionTool):
    """批量浏览器命令（宿主 shipyard-neo 原生执行）。"""

    name: str = "astrbot_execute_browser_batch"
    description: str = "Execute a browser command batch in the sandbox."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "commands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ordered browser commands.",
                },
                "timeout": {"type": "integer", "default": 60},
                "stop_on_error": {"type": "boolean", "default": True},
                "description": {
                    "type": "string",
                    "description": "Optional execution description.",
                },
                "tags": {"type": "string", "description": "Optional tags."},
                "learn": {
                    "type": "boolean",
                    "description": "Whether to mark execution as learn evidence.",
                    "default": False,
                },
                "include_trace": {
                    "type": "boolean",
                    "description": "Whether to include trace_ref in response.",
                    "default": False,
                },
            },
            "required": ["commands"],
        }
    )


@builtin_tool(config=_SHIPYARD_NEO_TOOL_CONFIG)
@dataclass
class RunBrowserSkillTool(FunctionTool):
    """按 skill_key 运行已发布浏览器技能（宿主 shipyard-neo 原生执行）。"""

    name: str = "astrbot_run_browser_skill"
    description: str = "Run a released browser skill in the sandbox by skill_key."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "skill_key": {"type": "string"},
                "timeout": {"type": "integer", "default": 60},
                "stop_on_error": {"type": "boolean", "default": True},
                "include_trace": {"type": "boolean", "default": False},
                "description": {"type": "string"},
                "tags": {"type": "string"},
            },
            "required": ["skill_key"],
        }
    )


__all__ = ["BrowserBatchExecTool", "BrowserExecTool", "RunBrowserSkillTool"]
