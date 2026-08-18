"""会话-对话管理器（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.conversation_mgr.ConversationManager` 的接口，
但数据全部存放在 Go 宿主（HostService RPC 反向调用），本模块只负责：
- 转发调用（new/switch/delete/get/update 等）到宿主桥；
- 维护 `session_conversations` 内存缓存（umo → 当前对话 ID）；
- 把宿主返回的会话 dict 包装成 `Conversation` 对象（插件访问
  conv.cid / conv.persona_id / conv.title / conv.updated_at 等属性）。
"""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

logger = logging.getLogger("astrbot")


def _to_timestamp(value: Any) -> float:
    """把宿主返回的会话时间统一为 Unix 时间戳（float）。

    宿主可能给出 int/float（秒级时间戳），也可能给出 RFC3339 字符串
    （time.Time 经 JSON 序列化），插件侧常 `datetime.fromtimestamp(conv.updated_at)`，
    这里统一兜底转换。
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except (ValueError, TypeError):
            pass
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.timestamp()
        except (ValueError, TypeError):
            return 0.0
    return 0.0


class Conversation:
    """包装宿主返回的会话 dict，提供属性访问（对齐 Python 本体的
    Conversation PO 类：cid/persona_id/title/history/created_at/updated_at
    等字段）。"""

    def __init__(self, data: dict | None = None) -> None:
        self._data: dict = data or {}
        self.cid: str = str(self._data.get("cid") or "")
        self.user_id: str = str(self._data.get("user_id") or "")
        self.platform_id: str = str(self._data.get("platform_id") or "")
        self.persona_id: Any = self._data.get("persona_id")
        self.title: str = str(self._data.get("title") or "")
        # 宿主会话快照不含历史记录；缺省为 "[]"（对齐本体历史 JSON 字符串）
        self.history: str = str(self._data.get("history") or "[]")
        self.created_at: float = _to_timestamp(self._data.get("created_at"))
        self.updated_at: float = _to_timestamp(self._data.get("updated_at"))
        self.is_deleted: bool = bool(self._data.get("is_deleted"))
        self.token_usage: int = int(self._data.get("token_usage") or 0)

    def __getitem__(self, key: str) -> Any:
        return self._data.get(key)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def to_dict(self) -> dict:
        return dict(self._data)

    def __repr__(self) -> str:
        return f"Conversation(cid={self.cid!r}, title={self.title!r}, persona_id={self.persona_id!r})"


class ConversationManager:
    """负责管理会话与 LLM 的对话，某个会话当前正在用哪个对话。

    数据经宿主桥（HostBridge）反向调用 Go 宿主会话管理器存储。
    """

    def __init__(self, bridge: Any | None = None) -> None:
        # bridge 可以是 HostBridge 实例，也可以是返回 HostBridge 的
        # 可调用对象（Context 传入 self._bridge 保持单桥来源一致）
        self._bridge_getter: Any = bridge
        self.session_conversations: dict[str, str] = {}
        # 会话删除回调函数列表（用于级联清理，如知识库配置）
        self._on_session_deleted_callbacks: list[Callable[[str], Awaitable[None]]] = []

    def _bridge(self):
        if self._bridge_getter is None:
            raise RuntimeError("宿主桥未就绪（ConversationManager 未绑定宿主）")
        if callable(self._bridge_getter):
            return self._bridge_getter()
        return self._bridge_getter

    def register_on_session_deleted(
        self,
        callback: Callable[[str], Awaitable[None]],
    ) -> None:
        """注册会话删除回调函数（对齐本体：会话删除时级联清理）。"""
        self._on_session_deleted_callbacks.append(callback)

    async def _trigger_session_deleted(self, unified_msg_origin: str) -> None:
        for callback in self._on_session_deleted_callbacks:
            try:
                await callback(unified_msg_origin)
            except Exception as e:
                logger.error(
                    f"会话删除回调执行失败 (session: {unified_msg_origin}): {e}"
                )

    async def new_conversation(
        self,
        unified_msg_origin: str,
        platform_id: str | None = None,
        content: list[dict] | None = None,
        title: str | None = None,
        persona_id: str | None = None,
    ) -> str:
        """新建对话，并将当前会话的对话转移到新对话。

        返回对话 ID（uuid 字符串）；宿主不可用时返回 ""。
        """
        if not platform_id:
            parts = unified_msg_origin.split(":")
            if len(parts) >= 3:
                platform_id = parts[0]
        if not platform_id:
            platform_id = "unknown"
        try:
            cid = await self._bridge().new_conversation_async(
                unified_msg_origin,
                platform_id or "",
                persona_id or "",
            )
        except Exception as e:
            logger.warning(f"new_conversation 失败: {e}")
            return ""
        if cid:
            self.session_conversations[unified_msg_origin] = cid
            # 宿主 NewConversation 不接收标题，创建后如有标题再补设
            if title:
                try:
                    await self._bridge().update_conversation_title_async(
                        unified_msg_origin,
                        title,
                        cid,
                    )
                except Exception as e:
                    logger.warning(f"new_conversation 设置标题失败: {e}")
        return cid

    async def switch_conversation(
        self,
        unified_msg_origin: str,
        conversation_id: str,
    ) -> None:
        """切换会话的对话。"""
        try:
            ok = await self._bridge().switch_conversation_async(
                unified_msg_origin,
                conversation_id,
            )
            if ok:
                self.session_conversations[unified_msg_origin] = conversation_id
        except Exception as e:
            logger.warning(f"switch_conversation 失败: {e}")

    async def delete_conversation(
        self,
        unified_msg_origin: str,
        conversation_id: str | None = None,
    ) -> None:
        """删除会话的对话；conversation_id 为 None 时删除当前对话。"""
        if not conversation_id:
            conversation_id = self.session_conversations.get(unified_msg_origin)
        if conversation_id:
            try:
                ok = await self._bridge().delete_conversation_async(
                    unified_msg_origin,
                    conversation_id,
                )
                if not ok:
                    logger.warning(
                        f"delete_conversation 失败或对话不存在: {conversation_id}"
                    )
            except Exception as e:
                logger.warning(f"delete_conversation 失败: {e}")
            curr_cid = await self.get_curr_conversation_id(unified_msg_origin)
            if curr_cid == conversation_id:
                self.session_conversations.pop(unified_msg_origin, None)

    async def delete_conversations_by_user_id(self, unified_msg_origin: str) -> None:
        """删除会话的所有对话（宿主不支持批量删除时逐条删除）。"""
        conversations = await self.get_conversations(unified_msg_origin)
        for conv in conversations:
            try:
                await self._bridge().delete_conversation_async(
                    unified_msg_origin,
                    conv.cid,
                )
            except Exception as e:
                logger.warning(f"删除对话 {conv.cid} 失败: {e}")
        self.session_conversations.pop(unified_msg_origin, None)
        # 触发会话删除回调（级联清理）
        await self._trigger_session_deleted(unified_msg_origin)

    async def get_curr_conversation_id(self, unified_msg_origin: str) -> str | None:
        """获取会话当前的对话 ID。"""
        ret = self.session_conversations.get(unified_msg_origin)
        if not ret:
            try:
                ret = await self._bridge().get_curr_conversation_id_async(
                    unified_msg_origin
                )
            except Exception as e:
                logger.warning(f"get_curr_conversation_id 失败: {e}")
                return None
            if ret:
                self.session_conversations[unified_msg_origin] = ret
        return ret or None

    async def get_conversation(
        self,
        unified_msg_origin: str,
        conversation_id: str,
        create_if_not_exists: bool = False,
    ) -> Conversation | None:
        """获取会话的对话；create_if_not_exists 为 True 时宿主会兜底创建。"""
        try:
            data = await self._bridge().get_conversation_async(
                unified_msg_origin,
                conversation_id or "",
                create_if_not_exists,
            )
        except Exception as e:
            logger.warning(f"get_conversation 失败: {e}")
            return None
        if not data:
            return None
        return Conversation(data)

    async def get_conversations(
        self,
        unified_msg_origin: str | None = None,
        platform_id: str | None = None,
    ) -> list[Conversation]:
        """获取对话列表（宿主按 unified_msg_origin 过滤；platform_id
        仅本地过滤，宿主数据源未提供该维度时忽略）。"""
        try:
            raw = await self._bridge().get_conversations_async(
                unified_msg_origin or ""
            )
        except Exception as e:
            logger.warning(f"get_conversations 失败: {e}")
            return []
        out: list[Conversation] = []
        for item in raw:
            if platform_id and str(item.get("platform_id") or "") != str(platform_id):
                continue
            out.append(Conversation(item))
        return out

    async def update_conversation(
        self,
        unified_msg_origin: str,
        conversation_id: str | None = None,
        history: list[dict] | None = None,
        title: str | None = None,
        persona_id: str | None = None,
        token_usage: int | None = None,
    ) -> None:
        """更新会话的对话（宿主仅支持标题/人格更新，history/token_usage 忽略）。"""
        if not conversation_id:
            conversation_id = await self.get_curr_conversation_id(unified_msg_origin)
        if not conversation_id:
            return
        if title is not None:
            await self.update_conversation_title(
                unified_msg_origin,
                title,
                conversation_id,
            )
        if persona_id is not None:
            await self.update_conversation_persona_id(
                unified_msg_origin,
                persona_id,
                conversation_id,
            )

    async def update_conversation_title(
        self,
        unified_msg_origin: str,
        title: str,
        conversation_id: str | None = None,
    ) -> None:
        """更新会话的对话标题。"""
        if not conversation_id:
            conversation_id = await self.get_curr_conversation_id(unified_msg_origin)
        if not conversation_id:
            logger.warning("update_conversation_title: 当前没有对话，无法更新标题")
            return
        try:
            await self._bridge().update_conversation_title_async(
                unified_msg_origin,
                title,
                conversation_id,
            )
        except Exception as e:
            logger.warning(f"update_conversation_title 失败: {e}")

    async def update_conversation_persona_id(
        self,
        unified_msg_origin: str,
        persona_id: str,
        conversation_id: str | None = None,
    ) -> None:
        """更新会话的对话 Persona ID。"""
        if not conversation_id:
            conversation_id = await self.get_curr_conversation_id(unified_msg_origin)
        if not conversation_id:
            logger.warning("update_conversation_persona_id: 当前没有对话，无法更新人格")
            return
        try:
            await self._bridge().update_conversation_persona_id_async(
                unified_msg_origin,
                persona_id,
                conversation_id,
            )
        except Exception as e:
            logger.warning(f"update_conversation_persona_id 失败: {e}")

    async def get_human_readable_context(
        self,
        unified_msg_origin: str,
        conversation_id: str,
        page: int = 1,
        page_size: int = 10,
    ) -> tuple[list[str], int]:
        """获取人类可读的上下文（User:/Assistant: 前缀），按页返回。"""
        conversation = await self.get_conversation(unified_msg_origin, conversation_id)
        if not conversation:
            return [], 0
        try:
            history = json.loads(conversation.history)
            if not isinstance(history, list):
                return [], 0
        except (ValueError, TypeError):
            return [], 0

        contexts_groups: list[list[str]] = []
        temp_contexts: list[str] = []
        for record in history:
            if not isinstance(record, dict):
                continue
            role = record.get("role")
            if role == "user":
                temp_contexts.append(f"User: {record.get('content')}")
            elif role == "assistant":
                if record.get("content"):
                    temp_contexts.append(f"Assistant: {record['content']}")
                elif "tool_calls" in record:
                    tool_calls_str = json.dumps(
                        record["tool_calls"],
                        ensure_ascii=False,
                    )
                    temp_contexts.append(f"Assistant: [函数调用] {tool_calls_str}")
                else:
                    temp_contexts.append("Assistant: [未知的内容]")
                contexts_groups.insert(0, temp_contexts)
                temp_contexts = []

        contexts = [item for sublist in contexts_groups for item in sublist]
        paged = contexts[(page - 1) * page_size : page * page_size]
        total_pages = len(contexts) // page_size
        if len(contexts) % page_size != 0:
            total_pages += 1
        return paged, total_pages
