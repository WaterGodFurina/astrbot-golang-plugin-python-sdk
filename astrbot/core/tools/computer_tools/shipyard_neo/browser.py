"""computer_tools.shipyard_neo.browser（Go 宿主兼容运行时，对齐本体）。

SDK 薄壳：浏览器编排工具类定义对齐本体，真实执行由宿主 shipyard-neo sandbox 完成。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.core.agent.tool import FunctionTool


@dataclass
class BrowserExecTool(FunctionTool):
    name: str = "astrbot_execute_browser"
    description: str = "Execute one browser automation command in the sandbox."
    parameters: dict = field(default_factory=dict)


@dataclass
class BrowserBatchExecTool(FunctionTool):
    name: str = "astrbot_execute_browser_batch"
    description: str = "Execute a browser command batch in the sandbox."
    parameters: dict = field(default_factory=dict)


@dataclass
class RunBrowserSkillTool(FunctionTool):
    name: str = "astrbot_run_browser_skill"
    description: str = "Run a released browser skill in the sandbox by skill_key."
    parameters: dict = field(default_factory=dict)


__all__ = ["BrowserBatchExecTool", "BrowserExecTool", "RunBrowserSkillTool"]