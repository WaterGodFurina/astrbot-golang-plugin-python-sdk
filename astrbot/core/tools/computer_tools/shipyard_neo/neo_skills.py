"""computer_tools.shipyard_neo.neo_skills（Go 宿主兼容运行时，对齐本体）。

SDK 薄壳：技能候选开发工具类的 name / description / parameters（schema）与
本体一致，真实执行由宿主 shipyard-neo 完成。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from astrbot.core.agent.tool import FunctionTool


@dataclass
class GetExecutionHistoryTool(FunctionTool):
    name: str = "astrbot_get_execution_history"
    description: str = "Get execution history from current sandbox."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "exec_type": {"type": "string"},
                "success_only": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "default": 100},
                "offset": {"type": "integer", "default": 0},
                "tags": {"type": "string"},
                "has_notes": {"type": "boolean", "default": False},
                "has_description": {"type": "boolean", "default": False},
            },
            "required": [],
        }
    )


@dataclass
class AnnotateExecutionTool(FunctionTool):
    name: str = "astrbot_annotate_execution"
    description: str = "Annotate one execution history record."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "execution_id": {"type": "string"},
                "description": {"type": "string"},
                "tags": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["execution_id"],
        }
    )


@dataclass
class CreateSkillPayloadTool(FunctionTool):
    name: str = "astrbot_create_skill_payload"
    description: str = (
        "Step 1/3 for Neo skill authoring: create immutable payload content and return payload_ref. "
        "Use this to store skill_markdown and structured metadata; do NOT write local skill folders directly."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "payload": {
                    "anyOf": [{"type": "object"}, {"type": "array", "items": {"type": "object"}}],
                    "description": "Skill payload JSON. Typical schema: {skill_markdown, inputs, outputs, meta}. This only stores content and returns payload_ref; it does not create a candidate or release.",
                },
                "kind": {
                    "type": "string",
                    "description": "Payload kind.",
                    "default": "astrbot_skill_v1",
                },
            },
            "required": ["payload"],
        }
    )


@dataclass
class GetSkillPayloadTool(FunctionTool):
    name: str = "astrbot_get_skill_payload"
    description: str = "Get one skill payload by payload_ref."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "payload_ref": {"type": "string"},
            },
            "required": ["payload_ref"],
        }
    )


@dataclass
class CreateSkillCandidateTool(FunctionTool):
    name: str = "astrbot_create_skill_candidate"
    description: str = (
        "Step 2/3 for Neo skill authoring: create a candidate by binding execution evidence "
        "(source_execution_ids) with skill identity (skill_key) and optional payload_ref."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "skill_key": {
                    "type": "string",
                    "description": "Stable logical identifier, e.g. image-collage-9grid.",
                },
                "source_execution_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Execution evidence IDs captured from sandbox history.",
                },
                "scenario_key": {
                    "type": "string",
                    "description": "Optional scenario namespace for grouping candidates.",
                },
                "payload_ref": {
                    "type": "string",
                    "description": "Optional payload reference created by astrbot_create_skill_payload.",
                },
            },
            "required": ["skill_key", "source_execution_ids"],
        }
    )


@dataclass
class ListSkillCandidatesTool(FunctionTool):
    name: str = "astrbot_list_skill_candidates"
    description: str = "List skill candidates."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "skill_key": {"type": "string"},
                "limit": {"type": "integer", "default": 100},
                "offset": {"type": "integer", "default": 0},
            },
            "required": [],
        }
    )


@dataclass
class EvaluateSkillCandidateTool(FunctionTool):
    name: str = "astrbot_evaluate_skill_candidate"
    description: str = "Evaluate a skill candidate."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "string"},
                "passed": {"type": "boolean"},
                "score": {"type": "number"},
                "benchmark_id": {"type": "string"},
                "report": {"type": "string"},
            },
            "required": ["candidate_id", "passed"],
        }
    )


@dataclass
class PromoteSkillCandidateTool(FunctionTool):
    name: str = "astrbot_promote_skill_candidate"
    description: str = (
        "Step 3/3 for Neo skill authoring: promote candidate to canary/stable release. "
        "If stage=stable and sync_to_local=true, payload.skill_markdown is synced to local SKILL.md automatically."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "string"},
                "stage": {
                    "type": "string",
                    "description": "Release stage: canary/stable",
                    "default": "canary",
                },
                "sync_to_local": {
                    "type": "boolean",
                    "description": "Only used with stage=stable. true means sync payload.skill_markdown to local SKILL.md; false means release remains Neo-side only.",
                    "default": True,
                },
            },
            "required": ["candidate_id"],
        }
    )


@dataclass
class ListSkillReleasesTool(FunctionTool):
    name: str = "astrbot_list_skill_releases"
    description: str = "List skill releases."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "skill_key": {"type": "string"},
                "active_only": {"type": "boolean", "default": False},
                "stage": {"type": "string"},
                "limit": {"type": "integer", "default": 100},
                "offset": {"type": "integer", "default": 0},
            },
            "required": [],
        }
    )


@dataclass
class RollbackSkillReleaseTool(FunctionTool):
    name: str = "astrbot_rollback_skill_release"
    description: str = "Rollback one skill release."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "release_id": {"type": "string"},
            },
            "required": ["release_id"],
        }
    )


@dataclass
class SyncSkillReleaseTool(FunctionTool):
    name: str = "astrbot_sync_skill_release"
    description: str = (
        "Sync stable Neo release payload to local SKILL.md and update mapping metadata."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "release_id": {"type": "string"},
                "skill_key": {"type": "string"},
                "require_stable": {"type": "boolean", "default": True},
            },
            "required": [],
        }
    )


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