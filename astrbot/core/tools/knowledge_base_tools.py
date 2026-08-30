"""知识库工具（Go 宿主兼容运行时，对齐本体 tools/knowledge_base_tools.py）。

SDK 薄壳：`KnowledgeBaseQueryTool` 类对齐本体 name/schema；宿主 knowledgebase
子系统（Go nanovec）原生执行检索。`retrieve_knowledge_base` 为方便函数，
通过宿主知识库管理器转发（未接入宿主时降级返回 None）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from astrbot.core.agent.tool import FunctionTool

logger = logging.getLogger("astrbot")


@dataclass
class KnowledgeBaseQueryTool(FunctionTool):
    """知识库检索工具（宿主 knowledgebase 子系统原生执行）。"""

    name: str = "astr_kb_search"
    description: str = (
        "Query the knowledge base for facts or relevant context. "
        "Use this tool when the user's question requires factual information, "
        "definitions, background knowledge, or previously indexed content. "
        "Only send short keywords or a concise question as the query."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A concise keyword query for the knowledge base.",
                },
            },
            "required": ["query"],
        }
    )


def check_all_kb(kb_list: list[Any] | None) -> bool:
    """检查知识库列表是否全为空（对齐本体 check_all_kb）。"""
    if not kb_list:
        return True
    return all(kb is None for kb in kb_list)


async def retrieve_knowledge_base(query: str, umo: str = "", context: Any = None) -> str | None:
    """检索知识库上下文（薄壳转发宿主知识库管理器）。

    context 为 SDK Context 实例（含 kb_manager）时可转发宿主；否则返回 None。
    """
    if context is None:
        return None
    kb_mgr = getattr(context, "kb_manager", None) or getattr(
        context, "knowledge_base_manager", None
    )
    if kb_mgr is None:
        return None
    try:
        kb_names = []
        cfg = context.get_config(umo=umo) if getattr(context, "get_config", None) else {}
        kb_names = (cfg or {}).get("kb_names", []) or []
        if not kb_names:
            return None
        result = await kb_mgr.retrieve(
            query=query,
            kb_names=kb_names,
            top_k_fusion=cfg.get("kb_fusion_top_k", 20),
            top_m_final=cfg.get("kb_final_top_k", 5),
        )
        if not result:
            return None
        formatted = result.get("context_text", "")
        return formatted if formatted else None
    except Exception:
        logger.warning("retrieve_knowledge_base 转发宿主失败（降级为 None）")
        return None


__all__ = ["KnowledgeBaseQueryTool", "check_all_kb", "retrieve_knowledge_base"]