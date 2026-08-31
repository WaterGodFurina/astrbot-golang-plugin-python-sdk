"""哨兵对象（对齐本体 astrbot/core/sentinels.py）。

本体 `astrbot.core.db` 等（db/__init__.py:30）依赖
`from astrbot.core.sentinels import NOT_GIVEN`；SDK 侧必须保证该 import
路径可用。NOT_GIVEN 用于区分"未传参"与"显式传 None"。
"""
NOT_GIVEN = object()

__all__ = ["NOT_GIVEN"]
