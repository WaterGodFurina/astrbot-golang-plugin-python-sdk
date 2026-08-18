"""Metric 指标（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.utils.metrics.Metric` 的接口签名，
SDK 无指标上报基础设施，upload() 为 no-op，保证插件代码
`asyncio.create_task(Metric.upload(...))` 不抛异常。
"""
from __future__ import annotations

from typing import Any


class Metric:
    """空实现指标类：upload 为 no-op（SDK 不采集/上报指标）。"""

    @staticmethod
    async def upload(**kwargs: Any) -> None:
        """上报指标（SDK 降级为 no-op，忽略全部参数）。"""
        return None