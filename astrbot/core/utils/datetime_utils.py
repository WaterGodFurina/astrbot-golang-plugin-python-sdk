"""时间/日期工具（对齐 Python 本体 astrbot.core.utils.datetime_utils）。"""
import uuid
from datetime import datetime, timezone


def generate_timestamp_id() -> str:
    """生成紧凑的基于时间戳的 ID。

    格式：本地时间 ``YYYYMMDDHHMMSSmmm`` 后接 4 位随机十六进制字符。
    """
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")[:-3]
    return f"{timestamp}_{uuid.uuid4().hex[:4]}"


def normalize_datetime_utc(dt: datetime | None) -> datetime | None:
    """将 datetime 规整为 UTC。

    无时区信息的 naive datetime 按 UTC 解释（对齐 SQLite 存储行为）。
    """
    if dt is None:
        return None
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_utc_isoformat(dt: datetime | None) -> str | None:
    """将 datetime 转 UTC ISO 8601 字符串（None 原样返回）。"""
    normalized = normalize_datetime_utc(dt)
    if normalized is None:
        return None
    return normalized.isoformat()


def to_utc_timestamp(dt: datetime | None) -> float | None:
    """将 datetime 转 UTC 时间戳（秒，None 原样返回）。"""
    normalized = normalize_datetime_utc(dt)
    if normalized is None:
        return None
    return normalized.timestamp()