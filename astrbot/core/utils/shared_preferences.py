"""SharedPreferences 共享偏好存储（对齐 Python 本体 astrbot.core.utils.shared_preferences）。

sp 是 AstrBot 插件生态中跨插件共享的偏好/记忆存储（作用域 scope 化：
session/umo/global/自定义）。Go 宿主没有对应的 DB 表给 Python 子进程访问，
这里用宿主数据目录下的 shared_preferences.json 持久化（ASTRBOT_DATA_PATH），
文件锁保护并发写。API 面对齐本体：get_async/put_async/remove_async/clear_async
+ session_* / global_* 快捷方法 + 旧同步方法（get/put/remove/clear）。
"""
import asyncio
import json
import os
import threading
from collections import defaultdict
from copy import deepcopy
from typing import Any, TypeVar, overload

from astrbot import logger
from astrbot.core.db.po import Preference
from astrbot.core.utils.deprecation import deprecated

from .astrbot_path import get_astrbot_data_path

_VT = TypeVar("_VT")
_MISSING = object()

_lock = threading.RLock()


class _TemporaryCacheCleaner:
    """轻量后台调度器：每 24 小时清空一次 temporary_cache。

    对齐原版 SharedPreferences 中 BackgroundScheduler 的
    clear_sp_temp_cache 定时任务；用守护线程实现，不引入第三方依赖。
    """

    _INTERVAL_SECONDS = 24 * 60 * 60

    def __init__(self, target) -> None:
        self._target = target
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="sp-temp-cache-cleaner",
            daemon=True,
        )
        self._started = False

    def start(self) -> None:
        """启动后台线程（幂等）。"""
        if self._started:
            return
        self._started = True
        self._thread.start()

    def shutdown(self, wait: bool = False) -> None:
        """停止后台线程。"""
        self._stop.set()
        if wait:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.wait(self._INTERVAL_SECONDS):
            try:
                self._target()
            except Exception as e:
                logger.warning("清空 temporary_cache 失败（忽略）: %s", e)


class SharedPreferences:
    def __init__(self) -> None:
        self.path = os.path.join(get_astrbot_data_path(), "shared_preferences.json")
        self._cache: dict[tuple[str, str, str], Any] = {}
        self._cache_initialized = False
        self._io_lock = asyncio.Lock()
        # 临时缓存：简单 dict，仅供插件进程内短时记忆使用；每 24 小时由
        # 后台线程自动清空（对齐原版 temporary_cache 的定时清理调度）。
        self.temporary_cache: dict[str, dict] = defaultdict(dict)
        self._scheduler = _TemporaryCacheCleaner(self._clear_temporary_cache)
        self._scheduler.start()

    def _clear_temporary_cache(self) -> None:
        """清空临时缓存（后台调度器每 24 小时调用一次）。"""
        self.temporary_cache.clear()

    async def initialize(self) -> None:
        """初始化缓存（幂等）：首次调用时从磁盘加载已持久化的偏好。

        SDK 写操作同步落盘，加载在首次读写时自动完成，本方法仅为对齐
        原版 API 提供显式初始化入口。
        """
        with _lock:
            self._ensure_cache()

    async def flush(self) -> None:
        """等待所有未落盘的写操作完成（SDK 写操作同步落盘，直接返回）。

        对齐原版 flush() 的语义，但 SDK 无需排队等待。
        """
        return None

    async def close(self) -> None:
        """停止后台临时缓存清理线程（对齐原版 close()）。"""
        self._scheduler.shutdown(wait=False)

    def _load(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save(self, data: dict) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def _ensure_cache(self) -> None:
        with _lock:
            if not self._cache_initialized:
                raw = self._load()
                for (scope, scope_id, key), val in raw.items():
                    if isinstance(val, dict) and set(val.keys()) == {"scope", "scope_id", "key", "value"}:
                        # 旧格式 {scope,scope_id,key,value} 直接展平
                        self._cache[(val["scope"], val["scope_id"], val["key"])] = val["value"]
                    else:
                        try:
                            s, sid, k = json.loads(scope), json.loads(scope_id), json.loads(key)
                        except Exception:
                            continue
                        self._cache[(s, sid, k)] = val
                self._cache_initialized = True

    def _persist(self) -> None:
        with _lock:
            raw = {
                json.dumps(k[0], ensure_ascii=False): {
                    "scope": k[0],
                    "scope_id": k[1],
                    "key": k[2],
                    "value": deepcopy(v),
                }
                for k, v in self._cache.items()
            }
        self._save(raw)

    async def get_async(self, scope: str, scope_id: str, key: str, default: _VT = None) -> _VT:
        with _lock:
            self._ensure_cache()
            value = self._cache.get((scope, scope_id, key), _MISSING)
            return default if value is _MISSING else deepcopy(value)

    async def range_get_async(
        self,
        scope: str,
        scope_id: str | None = None,
        key: str | None = None,
    ) -> list[Preference]:
        """按范围返回所有匹配条目（scope/scope_id/key 可为 None 表示不限）。

        value 属性为 dict，实际值在 value["val"] 中（对齐原版语义）。
        """
        with _lock:
            self._ensure_cache()
            values = [
                (s, sid, k, deepcopy(v))
                for (s, sid, k), v in self._cache.items()
                if s == scope
                and (scope_id is None or sid == scope_id)
                and (key is None or k == key)
            ]
        return [
            Preference(scope=s, scope_id=sid, key=k, value={"val": v})
            for s, sid, k, v in values
        ]

    async def put_async(self, scope: str, scope_id: str, key: str, value: Any) -> None:
        async with self._io_lock:
            with _lock:
                self._ensure_cache()
                self._cache[(scope, scope_id, key)] = deepcopy(value)
                self._persist()

    async def remove_async(self, scope: str, scope_id: str, key: str) -> None:
        async with self._io_lock:
            with _lock:
                self._ensure_cache()
                self._cache.pop((scope, scope_id, key), None)
                self._persist()

    async def clear_async(self, scope: str, scope_id: str) -> None:
        async with self._io_lock:
            with _lock:
                self._ensure_cache()
                keys = [k for k in self._cache if k[0] == scope and k[1] == scope_id]
                for k in keys:
                    self._cache.pop(k, None)
                self._persist()

    @overload
    async def session_get(
        self,
        umo: str,
        key: str,
        default: _VT = None,
    ) -> _VT: ...

    @overload
    async def session_get(
        self,
        umo: None,
        key: str,
        default: Any = None,
    ) -> list[Preference]: ...

    @overload
    async def session_get(
        self,
        umo: str,
        key: None,
        default: Any = None,
    ) -> list[Preference]: ...

    @overload
    async def session_get(
        self,
        umo: None,
        key: None,
        default: Any = None,
    ) -> list[Preference]: ...

    async def session_get(
        self,
        umo: str | None,
        key: str | None = None,
        default: _VT = None,
    ) -> _VT | list[Preference]:
        """获取会话（umo）范围的偏好设置。

        当 umo 或 key 为 None 时，返回该范围下的 Preference 列表
        （value["val"] 为实际值）。
        """
        if umo is None or key is None:
            return await self.range_get_async("umo", umo, key)
        return await self.get_async("umo", umo, key, default)

    async def session_put(self, umo: str, key: str, value: Any) -> None:
        await self.put_async("umo", umo, key, value)

    async def session_remove(self, umo: str, key: str) -> None:
        await self.remove_async("umo", umo, key)

    @overload
    async def global_get(self, key: None, default: Any = None) -> list[Preference]: ...

    @overload
    async def global_get(self, key: str, default: _VT = None) -> _VT: ...

    async def global_get(
        self,
        key: str | None,
        default: _VT = None,
    ) -> _VT | list[Preference]:
        """获取全局范围的偏好设置。

        当 key 为 None 时，返回全部全局偏好 Preference 列表
        （value["val"] 为实际值）。
        """
        if key is None:
            return await self.range_get_async("global", "global", key)
        return await self.get_async("global", "global", key, default)

    async def global_put(self, key: str, value: Any) -> None:
        await self.put_async("global", "global", key, value)

    async def global_remove(self, key: str) -> None:
        await self.remove_async("global", "global", key)

    # ── 旧同步 API（对齐本体 deprecated 方法，行为等价）──

    @deprecated(
        reason="Use get_async() instead. Plugins: use PluginKVStoreMixin.get_kv_data().",
        version="4.0.0",
    )
    def get(self, key: str, default: _VT = None, scope: str | None = None, scope_id: str | None = "") -> _VT:
        if scope_id == "":
            scope_id = "unknown"
        with _lock:
            self._ensure_cache()
            value = self._cache.get((scope or "unknown", scope_id or "unknown", key), _MISSING)
            return default if value is _MISSING or value is None else deepcopy(value)

    @deprecated(reason="Use range_get_async() instead.", version="4.0.0")
    def range_get(
        self,
        scope: str,
        scope_id: str | None = None,
        key: str | None = None,
    ) -> list[Preference]:
        """按范围返回所有匹配条目（同步版）。

        直接从内存缓存读取（不做 run_until_complete 落盘等待），避免在
        运行中的事件循环内再次进入事件循环；写入侧仍走异步持久化。
        """
        with _lock:
            self._ensure_cache()
            values = [
                (s, sid, k, deepcopy(v))
                for (s, sid, k), v in self._cache.items()
                if s == scope
                and (scope_id is None or sid == scope_id)
                and (key is None or k == key)
            ]
        return [
            Preference(scope=s, scope_id=sid, key=k, value={"val": v})
            for s, sid, k, v in values
        ]

    @deprecated(
        reason="Use put_async() instead. Plugins: use PluginKVStoreMixin.put_kv_data().",
        version="4.0.0",
    )
    def put(self, key, value, scope: str | None = None, scope_id: str | None = None) -> None:
        asyncio.get_event_loop().run_until_complete(
            self.put_async(scope or "unknown", scope_id or "unknown", key, value)
        )

    @deprecated(
        reason="Use remove_async() instead. Plugins: use PluginKVStoreMixin.delete_kv_data().",
        version="4.0.0",
    )
    def remove(self, key, scope: str | None = None, scope_id: str | None = None) -> None:
        asyncio.get_event_loop().run_until_complete(
            self.remove_async(scope or "unknown", scope_id or "unknown", key)
        )

    @deprecated(reason="Use clear_async() instead.", version="4.0.0")
    def clear(self, scope: str | None = None, scope_id: str | None = None) -> None:
        asyncio.get_event_loop().run_until_complete(
            self.clear_async(scope or "unknown", scope_id or "unknown")
        )


sp = SharedPreferences()