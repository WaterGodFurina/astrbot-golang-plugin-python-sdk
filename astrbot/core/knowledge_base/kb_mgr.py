"""知识库管理器（Go 宿主兼容运行时，对齐本体 kb_mgr）。

对齐 Python 本体 `astrbot.core.knowledge_base.kb_mgr.KnowledgeBaseManager`
的公开方法面（initialize / create_kb / get_kb / get_kb_by_name / delete_kb /
list_kbs / update_kb / upload_from_url / retrieve / load_kbs / terminate /
_format_context）。宿主 Go 侧（internal/knowledgebase，nanovec 向量库）原生
管理知识库；插件侧本类为薄壳：

- 数据操作经 HostBridge 的 KB RPC（KBRetrieve / KBUploadFromURL /
  KBListKBs）转发宿主（同步 RPC 经 asyncio.to_thread 移出常驻 loop）；
- 宿主不可用 / 宿主无该 RPC（旧版宿主）时优雅降级为既有占位行为
  （retrieve → None、upload_from_url → no-op 等），不抛异常；
- create_kb / update_kb / delete_kb 尚无对应 RPC，保持占位（签名对齐本体）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("astrbot")


def _host_bridge():
    """获取宿主桥（薄壳转发入口；不可用返回 None）。"""
    try:
        from astrbot.core.star.context import get_host_bridge

        return get_host_bridge()
    except Exception:
        return None


async def _bridge_call(bridge: Any, method: str, /, *args: Any, **kwargs: Any) -> Any:
    """调用宿主 bridge 方法（阻塞 RPC 经 to_thread 移出常驻 loop）。

    bridge 未提供该方法（旧版宿主桥）时返回 None（调用方降级）。
    """
    fn = getattr(bridge, method, None)
    if fn is None:
        logger.debug(f"宿主 bridge 未提供 {method}，知识库操作降级")
        return None
    return await asyncio.to_thread(fn, *args, **kwargs)


class KnowledgeBaseManager:
    """知识库管理器（SDK 薄壳：宿主 KB 原生管理，方法签名对齐本体）。"""

    def __init__(self, provider_manager: Any = None) -> None:
        # 本体 __init__(provider_manager: ProviderManager) 必传；SDK 宿主侧
        # 原生管理 KB，provider_manager 仅为签名兼容占位（默认 None）。
        self.provider_manager = provider_manager
        self._kbs: dict[str, Any] = {}

    async def initialize(self) -> None:
        """初始化知识库（SDK 薄壳：no-op，宿主 KB 原生初始化）。"""

    async def create_kb(
        self,
        kb_name: str,
        description: str | None = None,
        emoji: str | None = None,
        embedding_provider_id: str | None = None,
        rerank_provider_id: str | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        top_k_dense: int | None = None,
        top_k_sparse: int | None = None,
        top_m_final: int | None = None,
    ) -> Any:
        """创建知识库（SDK 薄壳：返回 None，宿主原生创建）。

        参数面与本体一致（本体 kb_mgr.py:87-99）：top_k_dense/top_k_sparse/
        top_m_final 为混合检索参数（本体默认 50/50/5），SDK 仅接受不生效。
        """
        return None

    async def get_kb(self, kb_id: str) -> Any | None:
        """按 ID 获取知识库（真实现：宿主 KBListKBs 客户端按 kb_id 过滤）。

        返回宿主 KnowledgeBase 元数据 dict（kb_id/kb_name/description/
        emoji/doc_count/chunk_count 等，字段以宿主结构为准）；未找到返回
        None。宿主不可用 / 无该 RPC 时回退本地占位表（语义等价 None）。
        """
        bridge = _host_bridge()
        if bridge is not None:
            try:
                kbs = await _bridge_call(bridge, "kb_list_kbs")
            except Exception as e:
                logger.debug(f"kb_list_kbs 桥接失败（回退本地占位表）: {e}")
                kbs = None
            if isinstance(kbs, list):
                for kb in kbs:
                    if (
                        isinstance(kb, dict)
                        and str(kb.get("kb_id") or "") == str(kb_id)
                    ):
                        return kb
                return None
        return self._kbs.get(kb_id)

    async def get_kb_by_name(self, kb_name: str) -> Any | None:
        """按名称获取知识库（真实现：宿主 KBListKBs 客户端按 kb_name 过滤）。

        返回宿主 KnowledgeBase 元数据 dict（形态同 get_kb）；未找到返回
        None。宿主不可用 / 无该 RPC 时回退本地占位表。
        """
        bridge = _host_bridge()
        if bridge is not None:
            try:
                kbs = await _bridge_call(bridge, "kb_list_kbs")
            except Exception as e:
                logger.debug(f"kb_list_kbs 桥接失败（回退本地占位表）: {e}")
                kbs = None
            if isinstance(kbs, list):
                for kb in kbs:
                    if not isinstance(kb, dict):
                        continue
                    name = str(kb.get("kb_name") or kb.get("name") or "")
                    if name == str(kb_name):
                        return kb
                return None
        for kb in self._kbs.values():
            if getattr(kb, "kb_name", None) == kb_name or getattr(kb, "name", None) == kb_name:
                return kb
        return None

    async def delete_kb(self, kb_id: str) -> bool:
        """删除知识库（SDK 薄壳：返回 False，宿主原生删除）。"""
        return self._kbs.pop(kb_id, None) is not None

    async def list_kbs(self) -> list[Any]:
        """列出全部知识库（真实现：宿主 KBListKBs，返回 dict 列表）。

        每项为宿主 KnowledgeBase 元数据 dict（形态见 get_kb docstring，
        保持返回 Any 以对齐本体签名）。宿主不可用 / 无该 RPC 时降级为
        本地占位列表（当前恒空）。
        """
        bridge = _host_bridge()
        if bridge is not None:
            try:
                kbs = await _bridge_call(bridge, "kb_list_kbs")
            except Exception as e:
                logger.debug(f"kb_list_kbs 桥接失败（降级本地列表）: {e}")
                kbs = None
            if isinstance(kbs, list):
                return [kb for kb in kbs if isinstance(kb, dict)]
        return list(self._kbs.values())

    async def update_kb(
        self,
        kb_id: str,
        kb_name: str,
        description: str | None = None,
        emoji: str | None = None,
        embedding_provider_id: str | None = None,
        rerank_provider_id: str | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        top_k_dense: int | None = None,
        top_k_sparse: int | None = None,
        top_m_final: int | None = None,
    ) -> Any | None:
        """更新知识库（SDK 薄壳：返回 None）。

        参数面与本体一致（本体 kb_mgr.py:187-200）：top_k_dense/top_k_sparse/
        top_m_final 为混合检索参数，SDK 仅接受不生效。
        """
        return None

    async def upload_from_url(
        self,
        kb_id: str,
        url: str,
        chunk_size: int = 512,
        chunk_overlap: int = 50,
        batch_size: int = 32,
        tasks_limit: int = 3,
        max_retries: int = 3,
        progress_callback=None,
    ) -> None:
        """从 URL 上传文档（真实现：转发宿主 KBUploadFromURL RPC）。

        chunk_size / chunk_overlap 透传宿主（<=0 用宿主默认）。batch_size /
        tasks_limit / max_retries 为本体签名兼容参数，proto 未覆盖、仅接受
        不生效。progress_callback 保留参数但不会触发：上传由宿主进程执行，
        SDK 侧回调对象无法跨进程传递（见模块 docstring）。

        宿主不可用 / 宿主无该 RPC 时降级为 no-op（不抛异常）。
        """
        bridge = _host_bridge()
        if bridge is None:
            return
        try:
            ok = await _bridge_call(
                bridge,
                "kb_upload_from_url",
                kb_id=kb_id,
                url=url,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
        except Exception as e:
            logger.debug(f"kb_upload_from_url 桥接失败（降级 no-op）: {e}")
            return
        if not ok:
            logger.debug("kb_upload_from_url 宿主侧失败（降级 no-op）")

    async def retrieve(
        self,
        query: str,
        kb_names: list[str],
        top_k_fusion: int = 20,
        top_m_final: int = 5,
    ) -> dict | None:
        """检索知识库（真实现：转发宿主 KBRetrieve RPC）。

        签名与本体一致（本体 kb_mgr.py:282-288），返回语义对齐本体：

        - 成功 → {"context_text": str, "results": list[dict]}（results 为
          宿主检索结果 dict 列表，结构以宿主为准）；
        - 无可检索知识库（kb_names 为空且无不可用项，宿主检索为空）→ {}；
        - 指定的知识库全部不可用（宿主 KBListKBs 中不存在）→ ValueError；
        - 检索已执行但无结果 → None。

        宿主不可用 / 宿主无 KB RPC 时降级返回 None（不抛异常）。
        """
        bridge = _host_bridge()
        if bridge is None:
            return None
        names = [str(n) for n in (kb_names or []) if str(n)]
        available = names
        if names:
            # 指定了知识库名：先校验其在宿主是否存在（对齐本体"全不可用
            # → ValueError"语义）；校验本身失败（旧宿主无 KBListKBs RPC）
            # 时按全部可用继续检索。
            try:
                kbs = await _bridge_call(bridge, "kb_list_kbs")
            except Exception as e:
                logger.debug(f"kb_list_kbs 可用性校验失败（按全部可用处理）: {e}")
                kbs = None
            if isinstance(kbs, list):
                known: set[str] = set()
                for kb in kbs:
                    if isinstance(kb, dict):
                        known.add(str(kb.get("kb_name") or kb.get("name") or ""))
                available = [n for n in names if n in known]
                if not available:
                    raise ValueError(f"知识库均不可用: {names}")
        try:
            data = await _bridge_call(
                bridge,
                "kb_retrieve",
                query=query,
                kb_names=available,
                top_k_fusion=top_k_fusion,
                top_m_final=top_m_final,
            )
        except Exception as e:
            logger.debug(f"kb_retrieve 桥接失败（降级）: {e}")
            return None
        if not isinstance(data, dict):
            return None
        results = data.get("results") or []
        context_text = data.get("context_text") or ""
        if not results and not context_text:
            # 本体语义：无可检索知识库（kb_names 空）→ {}；检索无果 → None
            return {} if not available else None
        return {"context_text": context_text, "results": results}

    def _format_context(self, results: list[Any]) -> str:
        """格式化知识上下文（对齐本体 kb_mgr.py:342-361，纯本地实现）。

        Args:
            results: 检索结果列表（每项需含 kb_name/doc_name/content/score
                属性，鸭子类型访问，与本体 RetrievalResult 字段一致）。

        Returns:
            str: 格式化的上下文文本。
        """
        lines = ["以下是相关的知识库内容,请参考这些信息回答用户的问题:\n"]

        for i, result in enumerate(results, 1):
            lines.append(f"【知识 {i}】")
            lines.append(f"来源: {result.kb_name} / {result.doc_name}")
            lines.append(f"内容: {result.content}")
            lines.append(f"相关度: {result.score:.2f}")
            lines.append("")

        return "\n".join(lines)

    async def load_kbs(self) -> None:
        """加载知识库（SDK 薄壳：no-op）。"""

    async def terminate(self) -> None:
        """终止知识库（SDK 薄壳：no-op）。"""
