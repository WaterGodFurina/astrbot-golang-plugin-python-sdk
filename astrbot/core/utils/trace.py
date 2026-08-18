"""TraceSpan（Go 宿主兼容运行时）。

对齐 Python 本体 `astrbot.core.utils.trace.TraceSpan` 的构造签名与 record
方法，保证插件代码访问 event.trace / event.span 不抛 AttributeError；
SDK 无 trace 上报基础设施，record() 仅记录到实例属性并打 debug 日志，
不真正上报。
"""
from __future__ import annotations

import logging
import uuid
from time import time
from typing import Any

logger = logging.getLogger("astrbot")


class TraceSpan:
    """轻量 trace 跨度对象。

    - span_id：随机生成（uuid4）
    - name / umo / sender_name / message_outline / started_at：描述跨度上下文
    - record(action, **fields)：记录一次动作（SDK 降级为 debug 日志）
    """

    def __init__(
        self,
        name: str,
        umo: str | None = None,
        sender_name: str | None = None,
        message_outline: str | None = None,
    ) -> None:
        self.span_id = str(uuid.uuid4())
        self.name = name
        self.umo = umo
        self.sender_name = sender_name
        self.message_outline = message_outline
        self.started_at = time()

    def record(self, action: str, **fields: Any) -> None:
        """记录一次动作。SDK 无 trace 上报基础设施，降级为 debug 日志。"""
        logger.debug(
            f"trace[{self.name}] span_id={self.span_id} action={action} fields={fields}"
        )