"""插件生命周期状态机（取代 threading.Event 门闩）。

时序：
    CREATED → BRIDGE_READY → GRPC_READY → HANDSHAKE_SENT → REGISTERING →
    REGISTERED → INSTANTIATING → RUNNING → STOPPING → STOPPED

语义（对齐旧 _ready/_registered/_instanced 门闩）：
    - mark_ready()    → set(REGISTERING)  阶段 A（插件 import）完成，放行宿主 Register
    - wait_registered → wait(REGISTERED)  宿主 Register RPC 完成
    - mark_registered → set(REGISTERED)   宿主 Register RPC 内最后一步
    - mark_instanced  → set(RUNNING)      阶段 B（实例化）完成
    - _wait_instanced → wait(RUNNING, 15) 命令/过滤器/钩子 RPC 前置等待

set() 允许任意迁移（非法迁移只记 warning，不做严格 DAG 校验，避免误伤）；
wait()/require() 基于状态顺序做等待与前置校验，错误消息带 expected/actual。
线程安全：threading.Condition。
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger("astrbot.lifecycle")


class InvalidLifecycleState(Exception):
    """生命周期前置校验失败。traceback 渲染为
    `InvalidLifecycleState: expected <STATE>, actual <STATE>`。"""


class LifecycleStateMachine:
    CREATED = "CREATED"
    BRIDGE_READY = "BRIDGE_READY"
    GRPC_READY = "GRPC_READY"
    HANDSHAKE_SENT = "HANDSHAKE_SENT"
    REGISTERING = "REGISTERING"
    REGISTERED = "REGISTERED"
    INSTANTIATING = "INSTANTIATING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"

    _ORDER = (
        CREATED,
        BRIDGE_READY,
        GRPC_READY,
        HANDSHAKE_SENT,
        REGISTERING,
        REGISTERED,
        INSTANTIATING,
        RUNNING,
        STOPPING,
        STOPPED,
    )
    _RANK = {s: i for i, s in enumerate(_ORDER)}

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._state = self.CREATED

    # ---- 核心 API ----
    def set(self, state: str) -> None:
        """推进状态。非法迁移（回退）记 warning 但允许，保持健壮。"""
        with self._cond:
            old = self._state
            if self._rank(state) < self._rank(old):
                logger.warning(
                    f"lifecycle: 状态回退 {old} -> {state}（已允许，请检查时序）"
                )
            self._state = state
            self._cond.notify_all()

    def state(self) -> str:
        with self._cond:
            return self._state

    def wait(self, min_state: str = REGISTERING, timeout: float | None = None) -> bool:
        """等待状态推进到 >= min_state，超时返回 False。

        默认 min_state=REGISTERING：兼容 Register RPC 内
        `self._ready.wait(timeout=120)` 的旧 Event 门闩语义。
        """
        target = self._rank(min_state)
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while self._rank(self._state) < target:
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    if not self._cond.wait(remaining):
                        return False
                else:
                    self._cond.wait()
            return True

    def require(self, min_state: str) -> None:
        """前置校验：当前状态不足抛 InvalidLifecycleState。"""
        actual = self.state()
        if self._rank(actual) < self._rank(min_state):
            raise InvalidLifecycleState(f"expected {min_state}, actual {actual}")

    @classmethod
    def _rank(cls, state: str) -> int:
        return cls._RANK.get(state, -1)

    # ---- 兼容 API（dispatch.PluginServiceServicer 委托；语义与原 Event 一致）----
    def mark_ready(self) -> None:
        self.set(self.REGISTERING)

    def mark_registered(self) -> None:
        self.set(self.REGISTERED)

    def wait_registered(self, timeout: float) -> bool:
        return self.wait(self.REGISTERED, timeout)

    def mark_instanced(self) -> None:
        self.set(self.RUNNING)

    def _wait_instanced(self, timeout: float = 15.0) -> bool:
        return self.wait(self.RUNNING, timeout)
