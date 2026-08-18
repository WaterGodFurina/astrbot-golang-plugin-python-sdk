"""Tool-Loop Agent Runner（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.agent.runners.tool_loop_agent_runner.
ToolLoopAgentRunner`。SDK 降级实现：仅保证 import 与基础属性可访问，
不执行真实 Agent 循环。
"""


class ToolLoopAgentRunner:
    """工具循环 Agent 运行器（SDK 降级：不做真实 Agent 循环）。"""

    def __init__(self) -> None:
        self._final_llm_resp = None

    async def reset(self, **kwargs) -> None:
        """重置运行器（SDK 降级：no-op）。"""

    async def step_until_done(self, max_steps: int = 30):
        """迭代执行 Agent 步骤直至完成（SDK 降级：不产出任何步骤）。"""
        return
        yield  # pragma: no cover - 保证该方法是异步生成器

    def get_final_llm_resp(self):
        """返回最终 LLM 响应（SDK 降级：恒为 None）。"""
        return self._final_llm_resp
