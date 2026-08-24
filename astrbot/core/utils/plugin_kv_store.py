import asyncio
import json
import os
import tempfile
import threading
from typing import TypeVar

SUPPORTED_VALUE_TYPES = int | float | str | bytes | bool | dict | list | None
_VT = TypeVar("_VT")

# 读-改-写互斥锁：并发 put/delete 各自 load 旧快照会互相覆盖对方的 key
_KV_LOCK = threading.Lock()


class PluginKVStoreMixin:
    """为插件提供键值存储功能的 Mixin 类（Go 宿主兼容运行时）。

    键值数据直接存到插件数据目录的 kv_store.json（宿主未提供 KV RPC）。
    """

    plugin_id: str

    def _kv_path(self) -> str:
        data_dir = os.environ.get(
            "ASTRBOT_PLUGIN_DATA_DIR",
            os.path.join(os.environ.get("ASTRBOT_DATA_PATH", os.getcwd()), "kv"),
        )
        path = os.path.join(data_dir, "kv_store.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def _kv_load(self) -> dict:
        path = self._kv_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _kv_save(self, data: dict) -> None:
        path = self._kv_path()
        # mkstemp 唯一临时文件 + os.replace 原子提交：宿主停插件发 SIGTERM，
        # 直接 open("w") 截断目标文件会留下半个 JSON，下次 _kv_load 静默
        # 返回 {} 导致插件 KV 数据全部丢失。
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def _mutate_sync(self, fn) -> None:
        with _KV_LOCK:
            data = self._kv_load()
            fn(data)
            self._kv_save(data)

    async def put_kv_data(self, key: str, value) -> None:
        await asyncio.to_thread(self._mutate_sync, lambda d: d.__setitem__(key, value))

    async def get_kv_data(self, key: str, default=None):
        return (await asyncio.to_thread(self._kv_load)).get(key, default)

    async def delete_kv_data(self, key: str) -> None:
        def _del(d: dict) -> None:
            d.pop(key, None)

        await asyncio.to_thread(self._mutate_sync, _del)
