"""启动 phase/错误行协议（stderr 输出）。

go-plugin 握手行打印到 stdout 之前绝不能污染 stdout（握手行之后 stdout 归
宿主 stdio 转发）。因此一切进度与错误行走 stderr（go-plugin 同样把 stderr
转发给宿主，宿主侧解析 [ASTRBOT] 前缀行）。格式：
  进度行：[ASTRBOT] phase=<name>
  错误行：[ASTRBOT] STARTUP_ERROR phase=<name> type=<ExceptionType> \
          plugin=<plugin> error=<单行消息>
错误消息折叠为单行（换行/控制字符 → 空格），并包含完整异常链
（"A: x caused by: B: y"）。
"""
from __future__ import annotations

import logging
import sys

logger = logging.getLogger("astrbot.progress")


def _fold(text: object) -> str:
    return " ".join(str(text).split())


def _chain_text(exc: BaseException) -> str:
    """把异常链压成单行文本：`TypeError: x caused by: ValueError: y`。"""
    parts: list[str] = []
    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        msg = str(cur).strip()
        parts.append(f"{type(cur).__name__}: {msg}" if msg else type(cur).__name__)
        cur = cur.__cause__ or cur.__context__
    return " caused by: ".join(parts)


def _emit(line: str) -> None:
    sys.stderr.write(line + "\n")
    sys.stderr.flush()


def emit_phase(name: str) -> None:
    """打一行进度：[ASTRBOT] phase=<name>（logger 同打一份）。"""
    line = f"[ASTRBOT] phase={name}"
    _emit(line)
    logger.info(line)


def emit_startup_error(phase: str, exc: BaseException, plugin: str = "") -> None:
    """打一行启动错误（单行，含完整异常链），logger 同打一份。"""
    line = (
        f"[ASTRBOT] STARTUP_ERROR phase={phase} type={type(exc).__name__} "
        f"plugin={_fold(plugin)} error={_fold(_chain_text(exc))}"
    )
    _emit(line)
    logger.error(line)
