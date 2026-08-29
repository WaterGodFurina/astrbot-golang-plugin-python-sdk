"""computer_tools.shipyard_neo.neo_skills（Go 宿主兼容运行时，对齐本体）。

SDK 薄壳：技能候选开发工具类定义对齐本体，真实执行由宿主 shipyard-neo 完成。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.core.agent.tool import FunctionTool


@dataclass
class GetExecutionHistoryTool(FunctionTool):
    name: str = "astrbot_get_execution_history"
    description: str = "Get execution history from current sandbox."
    parameters: dict = field(default_factory=dict)


@dataclass
class AnnotateExecutionTool(FunctionTool):
    name: str = "astrbot_annotate_execution"
    description: str = "Annotate one execution history record."
    parameters: dict = field(default_factory=dict)


@dataclass
class CreateSkillPayloadTool(FunctionTool):
    name: str = "astrbot_create_skill_payload"
    description: str = "Create a skill payload for a new skill candidate."
    parameters: dict = field(default_factory=dict)


@dataclass
class GetSkillPayloadTool(FunctionTool):
    name: str = "astrbot_get_skill_payload"
    description: str = "Get one skill payload by payload_ref."
    parameters: dict = field(default_factory=dict)


@dataclass
class CreateSkillCandidateTool(FunctionTool):
    name: str = "astrbot_create_skill_candidate"
    description: str = "Create a new skill candidate from a skill payload."
    parameters: dict = field(default_factory=dict)


@dataclass
class ListSkillCandidatesTool(FunctionTool):
    name: str = "astrbot_list_skill_candidates"
    description: str = "List skill candidates."
    parameters: dict = field(default_factory=dict)


@dataclass
class EvaluateSkillCandidateTool(FunctionTool):
    name: str = "astrbot_evaluate_skill_candidate"
    description: str = "Evaluate a skill candidate."
    parameters: dict = field(default_factory=dict)


@dataclass
class PromoteSkillCandidateTool(FunctionTool):
    name: str = "astrbot_promote_skill_candidate"
    description: str = "Promote a skill candidate to a release."
    parameters: dict = field(default_factory=dict)


@dataclass
class ListSkillReleasesTool(FunctionTool):
    name: str = "astrbot_list_skill_releases"
    description: str = "List skill releases."
    parameters: dict = field(default_factory=dict)


@dataclass
class RollbackSkillReleaseTool(FunctionTool):
    name: str = "astrbot_rollback_skill_release"
    description: str = "Rollback one skill release."
    parameters: dict = field(default_factory=dict)


@dataclass
class SyncSkillReleaseTool(FunctionTool):
    name: str = "astrbot_sync_skill_release"
    description: str = "Sync a skill release to the sandbox."
    parameters: dict = field(default_factory=dict)


__all__ = [
    "AnnotateExecutionTool",
    "CreateSkillCandidateTool",
    "CreateSkillPayloadTool",
    "EvaluateSkillCandidateTool",
    "GetExecutionHistoryTool",
    "GetSkillPayloadTool",
    "ListSkillCandidatesTool",
    "ListSkillReleasesTool",
    "PromoteSkillCandidateTool",
    "RollbackSkillReleaseTool",
    "SyncSkillReleaseTool",
]