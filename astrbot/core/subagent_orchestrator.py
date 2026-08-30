"""子 Agent 编排器（Go 宿主兼容运行时，对齐本体 subagent_orchestrator）。

从配置读取子代理定义并注册 handoff 工具。与本体一致：本类不执行 agent，
执行由 HandoffTool + 宿主 Agent 编排链完成。
"""
from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING, Any

from astrbot.core.agent.agent import Agent
from astrbot.core.agent.handoff import HandoffTool
from astrbot.core.agent.run_context import TContext
from astrbot.core.provider.func_tool_manager import FunctionToolManager

if TYPE_CHECKING:
    from astrbot.core.persona_mgr import PersonaManager

logger = logging.getLogger("astrbot")


class SubAgentOrchestrator:
    """加载子代理配置并注册 handoff 工具（对齐本体 SubAgentOrchestrator）。"""

    def __init__(
        self, tool_mgr: FunctionToolManager | None = None, persona_mgr: Any | None = None
    ) -> None:
        self._tool_mgr = tool_mgr
        self._persona_mgr = persona_mgr
        self.handoffs: list[HandoffTool] = []
        if self._tool_mgr is not None:
            for tool in self.handoffs:
                self._tool_mgr.func_list.append(tool)

    async def reload_from_config(self, cfg: dict | None) -> None:
        """从配置加载子代理并注册 handoff 工具（对齐本体 reload_from_config）。

        cfg 形如 {"agents": [{"name", "enabled", "persona_id", "system_prompt",
        "public_description", "provider_id", "tools"}]}；persona_id 命中本仓库
        人格管理器时取人格 prompt/tools 覆盖。SDK 薄壳：不依赖宿主（宿主
        Agent 编排链原生读取本配置语义）。
        """
        cfg = cfg or {}
        agents = cfg.get("agents", [])
        if not isinstance(agents, list):
            logger.warning("subagent_orchestrator.agents must be a list")
            return

        handoffs: list[HandoffTool] = []
        for item in agents:
            if not isinstance(item, dict):
                continue
            if not item.get("enabled", True):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue

            persona_id = item.get("persona_id")
            if persona_id is not None:
                persona_id = str(persona_id).strip() or None
            persona_data = None
            if self._persona_mgr is not None and persona_id:
                try:
                    persona_data = self._persona_mgr.get_persona_v3_by_id(persona_id)
                except Exception as e:
                    logger.warning(f"SubAgent persona 读取失败: {e}")
                    persona_data = None
            if persona_id and persona_data is None:
                logger.warning(
                    "SubAgent persona %s not found, fallback to inline prompt.",
                    persona_id,
                )

            instructions = str(item.get("system_prompt", "")).strip()
            public_description = str(item.get("public_description", "")).strip()
            provider_id = item.get("provider_id")
            if provider_id is not None:
                provider_id = str(provider_id).strip() or None
            tools = item.get("tools", [])
            begin_dialogs = None

            if persona_data and isinstance(persona_data, dict):
                prompt = str(persona_data.get("prompt", "")).strip()
                if prompt:
                    instructions = prompt
                begin_dialogs = copy.deepcopy(persona_data.get("_begin_dialogs_processed"))
                tools = persona_data.get("tools")
                if public_description == "" and prompt:
                    public_description = prompt[:120]
            if tools is None:
                tools = None
            elif not isinstance(tools, list):
                tools = []
            else:
                tools = [str(t).strip() for t in tools if str(t).strip()]

            agent = Agent[TContext](name=name, instructions=instructions, tools=tools)
            if begin_dialogs is not None:
                agent.begin_dialogs = begin_dialogs
            handoff = HandoffTool(agent=agent, tool_description=public_description or None)
            if provider_id:
                handoff.provider_id = provider_id
            handoffs.append(handoff)

        for handoff in handoffs:
            logger.info(f"Registered subagent handoff tool: {handoff.name}")
        self.handoffs = handoffs


__all__ = ["SubAgentOrchestrator"]