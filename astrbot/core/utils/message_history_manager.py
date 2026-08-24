"""平台消息历史管理器（Go 宿主兼容运行时）。

Go 宿主没有数据库 RPC，因此消息历史以 JSON 文件形式持久化到插件数据
目录（ASTRBOT_PLUGIN_DATA_DIR）下的 message_history.json，模式对齐
plugin_kv_store.py（读-改-写整文件，简单可靠）。

接口签名对齐 Python 原版 astrbot/core/platform_message_history_mgr.py 的
PlatformMessageHistoryManager：insert / get / delete / update / delete_by_id，
并额外提供 clear() 便于整体清理。
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

# 默认每个（platform_id, user_id）键保留的最大消息条数（0/None 表示不限制）
DEFAULT_MAX_MESSAGES = 2000
# 文件名（对齐 kv_store.json 的命名风格）
_HISTORY_FILE_NAME = "message_history.json"


def _data_dir() -> str:
    """返回插件数据目录（ASTRBOT_PLUGIN_DATA_DIR 优先，缺省回退 cwd）。"""
    return os.environ.get(
        "ASTRBOT_PLUGIN_DATA_DIR",
        os.path.join(os.environ.get("ASTRBOT_DATA_PATH", os.getcwd()), "data"),
    )


def _history_path() -> str:
    """返回 message_history.json 的绝对路径（父目录自动创建）。"""
    path = os.path.join(_data_dir(), _HISTORY_FILE_NAME)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


class PlatformMessageHistory:
    """单条消息历史记录（对齐原版 PO 的可读字段）。

    通过属性访问：id / platform_id / user_id / content / sender_id /
    sender_name / llm_checkpoint_id / created_at（tz-aware datetime，UTC）。
    """

    __slots__ = (
        "id",
        "platform_id",
        "user_id",
        "content",
        "sender_id",
        "sender_name",
        "llm_checkpoint_id",
        "created_at",
    )

    def __init__(
        self,
        id: int | None = None,
        platform_id: str = "",
        user_id: str = "",
        content: Any = None,
        sender_id: str | None = None,
        sender_name: str | None = None,
        llm_checkpoint_id: str | None = None,
        created_at: datetime | None = None,
    ) -> None:
        """初始化记录。"""
        self.id = id
        self.platform_id = platform_id
        self.user_id = user_id
        self.content = content
        self.sender_id = sender_id
        self.sender_name = sender_name
        self.llm_checkpoint_id = llm_checkpoint_id
        self.created_at = created_at or datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        """序列化为可 JSON 存储的 dict（created_at 转 ISO 字符串）。"""
        return {
            "id": self.id,
            "platform_id": self.platform_id,
            "user_id": self.user_id,
            "content": self.content,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "llm_checkpoint_id": self.llm_checkpoint_id,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PlatformMessageHistory":
        """从 JSON dict 还原记录（created_at 解析为 UTC datetime）。"""
        created_at = None
        raw_ts = data.get("created_at")
        if isinstance(raw_ts, str):
            try:
                created_at = datetime.fromisoformat(raw_ts)
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
            except ValueError:
                created_at = None
        return cls(
            id=data.get("id"),
            platform_id=str(data.get("platform_id", "") or ""),
            user_id=str(data.get("user_id", "") or ""),
            content=data.get("content"),
            sender_id=data.get("sender_id"),
            sender_name=data.get("sender_name"),
            llm_checkpoint_id=data.get("llm_checkpoint_id"),
            created_at=created_at or datetime.now(timezone.utc),
        )

    def __repr__(self) -> str:
        """调试输出。"""
        return (
            f"PlatformMessageHistory(id={self.id}, platform_id={self.platform_id!r}, "
            f"user_id={self.user_id!r}, sender_id={self.sender_id!r})"
        )


class PlatformMessageHistoryManager:
    """平台消息历史管理器（JSON 文件存储，Go 宿主兼容）。

    用法：
        await mgr.insert(platform_id=..., user_id=..., content=..., sender_id=..., sender_name=...)
        records = await mgr.get(platform_id=..., user_id=..., page=..., page_size=...)
    """

    def __init__(self) -> None:
        """初始化管理器（内存缓存 + JSON 文件持久化）。"""
        # 文件读写的互斥锁：不同协程/线程并发写时避免损坏 JSON
        self._lock = threading.Lock()
        # 内存缓存：file_key -> list[dict]，避免每次读都解析文件
        self._cache: dict[str, list[dict]] | None = None
        # 全局自增 ID 游标（从文件中恢复）
        self._next_id = 1

    # ── 存储层 ──────────────────────────────────────────────────────────
    def _load(self) -> dict[str, list[dict]]:
        """加载全部历史（含内存缓存与 ID 游标恢复）。"""
        if self._cache is not None:
            return self._cache
        try:
            with open(_history_path(), encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    data = {}
        except FileNotFoundError:
            data = {}
        except Exception:
            # 文件损坏/不可读时降级为空数据，不影响插件主流程
            data = {}
        self._cache = data
        max_id = 0
        for records in data.values():
            for record in records:
                try:
                    max_id = max(max_id, int(record.get("id", 0) or 0))
                except (TypeError, ValueError):
                    continue
        self._next_id = max_id + 1
        return data

    def _save(self) -> None:
        """将内存缓存整体写回 JSON 文件（mkstemp + os.replace 原子写）。"""
        if self._cache is None:
            return
        fd, tmp = tempfile.mkstemp(
            dir=os.path.dirname(_history_path()) or ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2)
            os.replace(tmp, _history_path())
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def _key(self, platform_id: str, user_id: str) -> str:
        """生成存储键：platform_id|user_id。"""
        return f"{platform_id}|{user_id}"

    @staticmethod
    def _record_id(r: dict) -> int:
        """安全读取记录 ID：外部工具写入的非数字 ID（"abc"/uuid）兜底为 -1，
        避免 delete_by_id/update 的 int() 转换抛 ValueError 中断整批处理。"""
        try:
            return int(r.get("id", -1) or -1)
        except (TypeError, ValueError):
            return -1

    # ── 写接口 ──────────────────────────────────────────────────────────
    async def insert(
        self,
        platform_id: str,
        user_id: str,
        content: Any,
        sender_id: str | None = None,
        sender_name: str | None = None,
        llm_checkpoint_id: str | None = None,
        max_messages: int | None = None,
    ) -> PlatformMessageHistory:
        """插入一条新的消息历史记录。

        Args:
            platform_id: 平台实例 ID。
            user_id: 会话来源 ID（群号/会话 ID）。
            content: 消息内容（dict 或 str；dict 结构由插件约定，如
                {"type": "user", "message": [...], "_qq_official": {...}}）。
            sender_id: 发送者 ID。
            sender_name: 发送者显示名。
            llm_checkpoint_id: LLM 检查点 ID（占位，原版语义）。
            max_messages: 每个 (platform_id, user_id) 键最多保留的条数，
                None 时使用默认值（不传则为 DEFAULT_MAX_MESSAGES）。
        """
        if max_messages is None:
            max_messages = DEFAULT_MAX_MESSAGES
        # content 必须是 JSON 可序列化的；dict 直接存，其它类型转字符串兜底
        try:
            json.dumps(content)
        except (TypeError, ValueError):
            content = str(content)

        def _insert_sync() -> PlatformMessageHistory:
            key = self._key(str(platform_id), str(user_id))
            # 记录的构造（含 _next_id 读取与自增）必须在锁内：并发 insert 时
            # 避免两个调用读到同一 _next_id 构造出重复 ID
            with self._lock:
                record = PlatformMessageHistory(
                    id=self._next_id,
                    platform_id=str(platform_id),
                    user_id=str(user_id),
                    content=content,
                    sender_id=sender_id,
                    sender_name=sender_name,
                    llm_checkpoint_id=llm_checkpoint_id,
                    created_at=datetime.now(timezone.utc),
                )
                data = self._load()
                records = data.setdefault(key, [])
                records.append(record.to_dict())
                # 超过上限时丢弃最旧的记录
                if max_messages and len(records) > max_messages:
                    del records[: len(records) - max_messages]
                self._next_id += 1
                self._save()
            return record

        # 锁 + 全量读写盘在子线程执行：历史文件接近上限时每次 insert 都是
        # 全量序列化 + 全量写，不能在事件循环线程同步执行。
        return await asyncio.to_thread(_insert_sync)

    # ── 读接口 ──────────────────────────────────────────────────────────
    async def get(
        self,
        platform_id: str,
        user_id: str,
        page: int = 1,
        page_size: int = 200,
    ) -> list[PlatformMessageHistory]:
        """获取指定会话的消息历史（最新在前，对齐原版 reverse 语义）。

        Args:
            platform_id: 平台实例 ID。
            user_id: 会话来源 ID。
            page: 页码（从 1 开始）。
            page_size: 每页条数。

        Returns:
            分页后的记录列表（新记录在前）。
        """
        def _get_sync() -> list[PlatformMessageHistory]:
            key = self._key(str(platform_id), str(user_id))
            with self._lock:
                data = self._load()
                records = list(data.get(key, []))
            # 最新的记录在列表尾部，翻转后最新在前（对齐原版 get() 行为）
            records.reverse()
            page = max(1, int(page))
            page_size = max(1, int(page_size))
            start = (page - 1) * page_size
            page_records = records[start : start + page_size]
            return [PlatformMessageHistory.from_dict(r) for r in page_records]

        return await asyncio.to_thread(_get_sync)

    async def delete(
        self, platform_id: str, user_id: str, offset_sec: int = 86400
    ) -> None:
        """删除指定会话中早于 offset_sec 秒的记录。"""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(0, int(offset_sec)))

        def _delete_sync() -> None:
            key = self._key(str(platform_id), str(user_id))
            with self._lock:
                data = self._load()
                records = data.get(key, [])
                kept = []
                for r in records:
                    created_at = None
                    raw_ts = r.get("created_at")
                    if isinstance(raw_ts, str):
                        try:
                            created_at = datetime.fromisoformat(raw_ts)
                        except ValueError:
                            created_at = None
                    if created_at is None or created_at >= cutoff:
                        kept.append(r)
                if len(kept) != len(records):
                    data[key] = kept
                    self._save()

        await asyncio.to_thread(_delete_sync)

    async def delete_by_id(self, message_id: int) -> None:
        """按记录 ID 删除一条消息历史。"""

        def _delete_by_id_sync() -> None:
            with self._lock:
                data = self._load()
                changed = False
                for key in list(data.keys()):
                    records = data[key]
                    new_records = [
                        r for r in records if self._record_id(r) != message_id
                    ]
                    if len(new_records) != len(records):
                        data[key] = new_records
                        changed = True
                if changed:
                    self._save()

        await asyncio.to_thread(_delete_by_id_sync)

    async def update(
        self,
        message_id: int,
        content: Any = None,
        llm_checkpoint_id: str | None = None,
    ) -> None:
        """更新一条消息历史记录的内容（content 为 None 表示不修改）。"""

        def _update_sync() -> None:
            with self._lock:
                data = self._load()
                for records in data.values():
                    for r in records:
                        if self._record_id(r) == message_id:
                            if content is not None:
                                r["content"] = content
                            if llm_checkpoint_id is not None:
                                r["llm_checkpoint_id"] = llm_checkpoint_id
                            self._save()
                            return

        await asyncio.to_thread(_update_sync)

    async def clear(self) -> None:
        """清空全部消息历史（插件卸载或重置时使用）。"""

        def _clear_sync() -> None:
            with self._lock:
                self._cache = {}
                self._next_id = 1
                self._save()

        await asyncio.to_thread(_clear_sync)
