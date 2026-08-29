"""定时任务工具（Go 宿主兼容运行时，对齐本体 tools/cron_tools.py）。

SDK 薄壳：FutureTaskTool 类定义对齐本体 name/schema，任务由宿主 cron
子系统原生执行（创建/编辑/列表/删除均走宿主 cron manager）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.core.agent.tool import FunctionTool

_FUTURE_TASK_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["create", "edit", "delete", "list"],
            "description": (
                "Action to perform. 'list' takes no parameters. 'delete' "
                "requires only 'job_id'. 'edit' requires 'job_id' plus the "
                "fields to change."
            ),
        },
        "name": {"type": "string", "description": "Optional task label."},
        "cron_expression": {
            "type": "string",
            "description": (
                "Cron expression for a recurring schedule, e.g. '0 8 * * *'."
            ),
        },
        "note": {
            "type": "string",
            "description": (
                "Detailed instructions for your future agent to execute when it wakes."
            ),
        },
        "run_once": {
            "type": "boolean",
            "description": "Run only once and delete after execution.",
        },
        "run_at": {
            "type": "string",
            "description": "ISO datetime for one-time execution.",
        },
        "job_id": {
            "type": "string",
            "description": "Task ID. Required for 'delete' and 'edit'.",
        },
    },
    "required": ["action"],
}


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