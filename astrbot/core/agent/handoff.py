"""Agent 移交工具（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.agent.handoff.HandoffTool`：继承
FunctionTool 的工具子类，把任务移交给另一个子代理。构造
`transfer_to_<agent.name>` 工具（name / parameters / description 与本体
一致），工具注册与执行全部由宿主 Agent 编排链完成，插件仅构造/继承本类。
"""
from __future__ import annotations

from typing import Any, Generic

from astrbot.core.agent.tool import FunctionTool
from astrbot.core.agent.run_context import TContext


class HandoffTool(FunctionTool, Generic[TContext]):
    """Handoff 工具（对齐本体：FunctionTool 子类 + 泛型）。

    构造参数与本体一致：``agent`` 即子代理目标（需有 ``name`` 属性）；
    工具名固定为 ``transfer_to_<agent.name>``，供 Agent 编排链识别。
    """

    def __init__(
        self,
        agent: Any,
        parameters: dict | None = None,
        tool_description: str | None = None,
        **kwargs: Any,
    ) -> None:
        # `tool_description` 是展示给主 LLM 的公开描述（与 FunctionTool 的
        # description 解耦，避免 kwargs 冲突——对齐本体）。
        description = tool_description or self.default_description(
            getattr(agent, "name", None)
        )
        super().__init__(
            name=f"transfer_to_{getattr(agent, 'name', 'another')}",
            parameters=parameters or self.default_parameters(),
            description=description,
            **kwargs,
        )
        # 子代理可选的 chat provider 覆盖（对齐本体：非空时移交使用该
        # provider 而非全局默认；Go 宿主编排链读取）。
        self.provider_id: str | None = None
        # 对齐本体：super().__init__() 之后赋值，避免父类覆盖该属性
        self.agent: Any = agent

    def default_parameters(self) -> dict:
        """默认参数 schema（与本体完全一致）：input 任务说明 /
        image_urls 多模态引用 / background_task 后台标志。"""
        return {
            "type": "object",
            "properties": {
                "input": {
                    "type": "string",
                    "description": (
                        "The input to be handed off to another agent. "
                        "This should be a clear and concise request or task."
                    ),
                },
                "image_urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional: An array of image sources (public HTTP URLs or "
                        "local file paths) used as references in multimodal tasks "
                        "such as video generation."
                    ),
                },
                "background_task": {
                    "type": "boolean",
                    "description": (
                        "Defaults to false. Set to true if the task may take "
                        "noticeable time, involves external tools, or the user "
                        "does not need to wait. Use false only for quick, "
                        "immediate tasks."
                    ),
                },
            },
        }

    def default_description(self, agent_name: str | None) -> str:
        """默认描述：把任务移交给另一个子代理。"""
        agent_name = agent_name or "another"
        return f"Delegate tasks to {agent_name} agent to handle the request."


__all__ = ["HandoffTool"]
