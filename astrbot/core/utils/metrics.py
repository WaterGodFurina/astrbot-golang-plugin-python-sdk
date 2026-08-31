"""Metric 指标（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.utils.metrics.Metric` 的公开方法面：
upload / flush / get_installation_id。SDK 无指标上报基础设施（本体经
aiohttp 上报 TickStats），upload()/flush() 为 no-op；get_installation_id
保留真实现（读写 ~/.astrbot/.installation_id），保证插件侧引用不炸。
"""
from __future__ import annotations

import os
import uuid
from typing import Any


class Metric:
    """空实现指标类：upload/flush 为 no-op（SDK 不采集/上报指标）。"""

    _iid_cache: str | None = None

    @staticmethod
    def get_installation_id() -> str:
        """获取或创建唯一安装 ID（对齐本体 get_installation_id）。

        持久化在 ``~/.astrbot/.installation_id``；读写失败时缓存并返回
        ``"null"``（与本体降级行为一致）。
        """
        if Metric._iid_cache is not None:
            return Metric._iid_cache

        config_dir = os.path.join(os.path.expanduser("~"), ".astrbot")
        id_file = os.path.join(config_dir, ".installation_id")

        if os.path.exists(id_file):
            try:
                with open(id_file) as f:
                    Metric._iid_cache = f.read().strip()
                    return Metric._iid_cache
            except Exception:
                pass
        try:
            os.makedirs(config_dir, exist_ok=True)
            installation_id = str(uuid.uuid4())
            with open(id_file, "w") as f:
                f.write(installation_id)
            Metric._iid_cache = installation_id
            return installation_id
        except Exception:
            Metric._iid_cache = "null"
            return "null"

    @staticmethod
    async def upload(**kwargs: Any) -> None:
        """上传非敏感指标（SDK 降级为 no-op，忽略全部参数）。

        本体签名 ``upload(**kwargs)``：SDK 保留 ``**kwargs`` 形态，
        插件以任意关键字参数调用（如 llm_tick/msg_event_tick）均不炸。
        """
        return None

    @staticmethod
    async def flush() -> None:
        """立即上报待发指标（SDK 降级为 no-op，与本体 flush 签名一致）。"""
        return None