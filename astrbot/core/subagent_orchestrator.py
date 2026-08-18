"""子 Agent 编排器（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.subagent_orchestrator.SubAgentOrchestrator`。
SDK 降级实现：仅保证 import 与构造不报错。
"""


class SubAgentOrchestrator:
    """子 Agent 编排器（SDK 降级）。"""

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def run(self, *args, **kwargs):
        """运行子 Agent（SDK 降级：返回 None）。"""
        return None
