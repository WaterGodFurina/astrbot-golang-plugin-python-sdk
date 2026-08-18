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
from copy import deepcopy
from typing import Any, TypeVar

from .astrbot_path import get_astrbot_data_path

_VT = TypeVar("_VT")
_MISSING = object()

_lock = threading.RLock()


class SharedPreferences:
    def __init__(self) -> None:
        self.path = os.path.join(get_astrbot_data_path(), "shared_preferences.json")
        self._cache: dict[tuple[str, str, str], Any] = {}
        self._cache_initialized = False
        self._io_lock = asyncio.Lock()

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

    async def session_get(self, umo: str, key: str, default: _VT = None) -> _VT:
        return await self.get_async("umo", umo, key, default)

    async def session_put(self, umo: str, key: str, value: Any) -> None:
        await self.put_async("umo", umo, key, value)

    async def session_remove(self, umo: str, key: str) -> None:
        await self.remove_async("umo", umo, key)

    async def global_get(self, key: str, default: _VT = None) -> _VT:
        return await self.get_async("global", "global", key, default)

    async def global_put(self, key: str, value: Any) -> None:
        await self.put_async("global", "global", key, value)

    async def global_remove(self, key: str) -> None:
        await self.remove_async("global", "global", key)

    # ── 旧同步 API（对齐本体 deprecated 方法，行为等价）──

    def get(self, key: str, default: _VT = None, scope: str | None = None, scope_id: str | None = "") -> _VT:
        if scope_id == "":
            scope_id = "unknown"
        with _lock:
            self._ensure_cache()
            value = self._cache.get((scope or "unknown", scope_id or "unknown", key), _MISSING)
            return default if value is _MISSING or value is None else deepcopy(value)

    def put(self, key, value, scope: str | None = None, scope_id: str | None = None) -> None:
        asyncio.get_event_loop().run_until_complete(
            self.put_async(scope or "unknown", scope_id or "unknown", key, value)
        )

    def remove(self, key, scope: str | None = None, scope_id: str | None = None) -> None:
        asyncio.get_event_loop().run_until_complete(
            self.remove_async(scope or "unknown", scope_id or "unknown", key)
        )

    def clear(self, scope: str | None = None, scope_id: str | None = None) -> None:
        asyncio.get_event_loop().run_until_complete(
            self.clear_async(scope or "unknown", scope_id or "unknown")
        )


sp = SharedPreferences()