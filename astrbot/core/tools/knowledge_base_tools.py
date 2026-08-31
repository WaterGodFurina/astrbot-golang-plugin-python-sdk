"""知识库工具（Go 宿主兼容运行时，对齐本体 tools/knowledge_base_tools.py）。

SDK 薄壳：`KnowledgeBaseQueryTool` 类对齐本体 name/description/parameters
（schema）并注册进内置工具注册表；agent 循环中的真实检索由宿主 knowledgebase
子系统（Go nanovec，internal/pipeline/kb_tools.go executeKBSearch）原生执行。

SDK 侧同时保留与本体同名的公共函数与兜底逻辑：
- ``check_all_kb``：纯函数，语义与本体一致（None 计数告警 + 库空判定）；
- ``retrieve_knowledge_base``：经 Context.kb_manager 转发（SDK kb_mgr 为
  降级实现，检索恒返回 None）；
- ``KnowledgeBaseQueryTool.call``：插件直接调用时走上述转发，无结果时
  返回与本体一致的 "No relevant knowledge found." 提示。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from astrbot.core.agent.tool import FunctionTool
from astrbot.core.tools.registry import builtin_tool

logger = logging.getLogger("astrbot")

# 对齐本体 knowledge_base_tools.py:12-14 的装饰器 config。
_KNOWLEDGE_BASE_TOOL_CONFIG = {
    "kb_agentic_mode": True,
}


@builtin_tool(config=_KNOWLEDGE_BASE_TOOL_CONFIG)
@dataclass
class KnowledgeBaseQueryTool(FunctionTool):
    """知识库检索工具（agent 循环中由宿主 knowledgebase 子系统原生执行）。

    插件直接实例化并调用 call() 时为降级形态：经 SDK kb_manager 转发
    （SDK kb_mgr 检索恒为 None），因此返回与本体 retrieve 无结果时一致
    的 "No relevant knowledge found."。
    """

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

    async def call(self, context, **kwargs):
        """兜底执行（对齐本体 call 语义，检索走 Context.kb_manager）。"""
        query = kwargs.get("query", "")
        if not query:
            return "error: Query parameter is empty."
        event_ctx = getattr(context, "context", None)
        result = await retrieve_knowledge_base(
            query=query,
            umo=getattr(getattr(event_ctx, "event", None), "unified_msg_origin", "")
            or "",
            context=getattr(event_ctx, "context", None),
        )
        if not result:
            return "No relevant knowledge found."
        return result


def check_all_kb(kb_list: list[Any] | None) -> bool:
    """检查知识库列表是否全为空（语义对齐本体 knowledge_base_tools.py:17-37）。

    Args:
        kb_list: 知识库实例列表，可能包含 None（未找到的知识库）。

    Returns:
        bool: True 表示所有知识库都为空或未找到（doc_count 与 chunk_count
        均为 0）。存在任一非空库时返回 False；列表为空/None 视为全空。
    """
    if not kb_list:
        return True

    none_count = sum(1 for kb in kb_list if kb is None)
    if none_count > 0:
        logger.warning(
            "[知识库] %d/%d 个知识库未找到或未加载，请检查配置中的知识库名称或 ID 是否正确",
            none_count,
            len(kb_list),
        )

    # 与本体一致：仅当所有非 None 库的 doc_count 与 chunk_count 都为 0 时
    # 才视为"全空"（本体访问 kb_helper.kb.doc_count；SDK 对无 .kb 包装的
    # 对象退化为自身，属性缺失按 0 兜底，保证鸭子类型访问不炸；KB 宿主
    # 桥真实现返回 dict 形态时按 kb.get 取键）。
    def _kb_counters(kb: Any) -> tuple[int, int]:
        inner = getattr(kb, "kb", None)
        if inner is None:
            inner = kb
        if isinstance(inner, dict):
            return (
                inner.get("doc_count", 0) or 0,
                inner.get("chunk_count", 0) or 0,
            )
        return (
            getattr(inner, "doc_count", 0) or 0,
            getattr(inner, "chunk_count", 0) or 0,
        )

    return not any(
        kb and (doc != 0 or chunk != 0) for kb, (doc, chunk) in
        ((kb, _kb_counters(kb)) for kb in kb_list if kb is not None)
    )


async def retrieve_knowledge_base(
    query: str,
    umo: str = "",
    context: Any = None,
) -> str | None:
    """检索知识库上下文（薄壳转发 Context.kb_manager，语义对齐本体）。

    与本体一致支持两级配置：会话级 ``kb_config``（sp.session_get）优先，
    其次全局配置 ``kb_names``；无可用库或检索无结果时返回 None。
    SDK kb_manager.retrieve 经宿主 KBRetrieve RPC 真实现；宿主不可用时
    降级为 None。
    """
    if context is None:
        return None
    kb_mgr = getattr(context, "kb_manager", None) or getattr(
        context, "knowledge_base_manager", None
    )
    if kb_mgr is None:
        return None
    try:
        from astrbot.core.utils.shared_preferences import sp

        cfg = context.get_config(umo=umo) if getattr(context, "get_config", None) else {}
        cfg = cfg or {}

        top_k = 5
        session_config = await sp.session_get(umo, "kb_config", default={})
        if session_config and "kb_ids" in session_config:
            kb_ids = session_config.get("kb_ids", []) or []
            if not kb_ids:
                logger.info("[知识库] 会话 %s 已被配置为不使用知识库", umo)
                return None
            kb_names = []
            for kb_id in kb_ids:
                kb_helper = await kb_mgr.get_kb(kb_id)
                if kb_helper:
                    # KB 宿主桥真实现返回 dict 形态（宿主 KnowledgeBase JSON）
                    if isinstance(kb_helper, dict):
                        name = str(kb_helper.get("kb_name") or "")
                    else:
                        name = getattr(kb_helper.kb, "kb_name", "") or ""
                    kb_names.append(name)
                else:
                    logger.warning("[知识库] 知识库不存在或未加载: %s", kb_id)
            kb_names = [name for name in kb_names if name]
            if not kb_names:
                return None
            logger.debug("[知识库] 使用会话级配置，知识库数量: %d", len(kb_names))
        else:
            kb_names = cfg.get("kb_names", []) or []
            top_k = cfg.get("kb_final_top_k", 5)

        top_k_fusion = cfg.get("kb_fusion_top_k", 20)
        if not kb_names:
            return None

        all_kbs = [await kb_mgr.get_kb_by_name(kb) for kb in kb_names]
        if check_all_kb(all_kbs):
            logger.debug("所配置的所有知识库全为空，跳过检索过程")
            return None

        kb_context = await kb_mgr.retrieve(
            query=query,
            kb_names=kb_names,
            top_k_fusion=top_k_fusion,
            top_m_final=top_k,
        )
        if not kb_context:
            return None

        formatted = kb_context.get("context_text", "")
        return formatted if formatted else None
    except Exception:
        logger.warning("retrieve_knowledge_base 转发宿主失败（降级为 None）")
        return None


__all__ = ["KnowledgeBaseQueryTool", "check_all_kb", "retrieve_knowledge_base"]
