"""字符串常用工具（对齐 Python 本体 astrbot.core.utils.string_utils）。

原版仅提供 normalize_and_dedupe_strings；这里额外补充插件高频用到的
extract_qq / clean_text 等简单函数。
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

_QQ_RE = re.compile(r"\b[1-9]\d{4,14}\b")


def normalize_and_dedupe_strings(items: Iterable[Any] | None) -> list[str]:
    """字符串列表规范化 + 去重（对齐原版）。

    忽略 None 与非字符串元素；空串及重复项剔除，返回去除首尾空白后的列表。
    """
    if items is None:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def extract_qq(text: str) -> list[str]:
    """从文本中提取 QQ 号列表（5-15 位数字，首位非 0，去重保序）。

    匹配不到时返回空列表。
    """
    if not text:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for m in _QQ_RE.finditer(text):
        qq = m.group(0)
        if qq not in seen:
            seen.add(qq)
            result.append(qq)
    return result


def clean_text(text: str) -> str:
    """清理文本：折叠空白并去除首尾空格。

    对常见网络消息格式（如 CQ 码残渣后的多空格）做简单规整。
    """
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()