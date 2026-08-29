"""Agent 运行钩子（Go 宿主兼容运行时，对齐本体 astr_agent_hooks）。

`MAIN_AGENT_HOOKS` 是本体的默认钩子实例：宿主 Agent 编排链在主 Agent
前后调用已注册的事件钩子（on_agent_begin / on_agent_done /
on_using_llm_tool / on_llm_tool_respond 等）。SDK 提供可实例化的薄壳，
保证插件 import 与构造不报错；插件侧事件钩子经
`register_on_agent_begin` 等装饰器在宿主跑，本类不直接触发宿主。
"""
from __future__ import annotations

from astrbot.core.agent.hooks import BaseAgentRunHooks


class MainAgentHooks(BaseAgentRunHooks):
    """主 Agent 默认钩子（SDK 薄壳：全部 no-op，宿主编排链触发事件钩子）。"""


class EmptyAgentHooks(BaseAgentRunHooks):
    """空钩子（对齐本体 EmptyAgentHooks）。"""


# 本体的默认主 Agent 钩子实例（插件的 on_agent_begin/on_agent_done 等
# 事件钩子由宿主编排链调用，插件侧仅保留可 import 的实例）。
MAIN_AGENT_HOOKS = MainAgentHooks()


__all__ = ["EmptyAgentHooks", "MAIN_AGENT_HOOKS", "MainAgentHooks"]