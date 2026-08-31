"""定时任务工具（Go 宿主兼容运行时，对齐本体 tools/cron_tools.py）。

SDK 薄壳：FutureTaskTool 类定义对齐本体 name/schema 并经 ``builtin_tool``
注册（config 规则同本体 _CRON_TOOL_CONFIG）；任务由宿主 cron 子系统原生
执行（internal/pipeline/cron_tools.go executeFutureTask，创建/编辑/列表/
删除均走宿主 cron manager）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.core.agent.tool import FunctionTool
from astrbot.core.tools.registry import builtin_tool

# 对齐本体 cron_tools.py:16-18 的装饰器 config（启用规则纯声明，宿主
# Go 侧按 provider_settings.proactive_capability.add_cron_tools 装配）。
_CRON_TOOL_CONFIG = {
    "provider_settings.proactive_capability.add_cron_tools": True,
}

_FUTURE_TASK_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["create", "edit", "delete", "list"],
            "description": "Action to perform. 'list' takes no parameters. 'delete' requires only 'job_id'. 'edit' requires 'job_id' plus the fields to change.",
        },
        "name": {
            "type": "string",
            "description": "Optional task label.",
        },
        "cron_expression": {
            "type": "string",
            "description": "Cron expression for a recurring schedule, e.g. '0 8 * * *' or '0 23 * * mon-fri'. Prefer named weekdays like 'mon-fri' or 'sat,sun' over numeric ranges like '1-5'.",
        },
        "note": {
            "type": "string",
            "description": "Detailed instructions for your future agent to execute when it wakes.",
        },
        "run_once": {
            "type": "boolean",
            "description": "Run only once and delete after execution. Use with run_at.",
        },
        "run_at": {
            "type": "string",
            "description": "ISO datetime for one-time execution, e.g. 2026-02-02T08:00:00+08:00.",
        },
        "job_id": {
            "type": "string",
            "description": "Task ID. Required for 'delete' and 'edit'.",
        },
    },
    "required": ["action"],
}


@builtin_tool(config=_CRON_TOOL_CONFIG)
@dataclass
class FutureTaskTool(FunctionTool):
    """定时任务管理工具（宿主 cron 子系统原生执行）。"""

    name: str = "future_task"
    description: str = (
        "Manage your future tasks. Use action='create' to schedule a recurring "
        "cron task or one-time run_at task. Use action='edit' to update an "
        "existing task. Use action='list' to inspect existing tasks. Use "
        "action='delete' to remove a task by job_id."
    )
    parameters: dict = field(default_factory=lambda: _FUTURE_TASK_SCHEMA)


__all__ = ["FutureTaskTool"]