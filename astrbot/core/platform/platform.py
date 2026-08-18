"""平台基类（对齐 Python 原版 v4.27.3 astrbot/core/platform/platform.py）。

Go 宿主兼容运行时：平台实例体系在 Go 侧，本基类不参与实际平台调度，仅对齐
原版签名/字段/方法供插件引用与子类化。除 run/meta 外全部给出默认实现或空
实现，避免插件因 NotImplementedError 崩坏。
"""

from __future__ import annotations

import abc
import asyncio
import logging
import uuid
from asyncio import Queue
from collections.abc import Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage
from astrbot.core.platform.message_session import MessageSesion
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.utils.metrics import Metric

logger = logging.getLogger("astrbot")


class PlatformStatus(Enum):
    """平台运行状态"""

    PENDING = "pending"  # 待启动
    RUNNING = "running"  # 运行中
    ERROR = "error"  # 发生错误
    STOPPED = "stopped"  # 已停止


@dataclass
class PlatformError:
    """平台错误信息。

    字段对齐 Python 本体（message/timestamp/traceback），同时保留
    SDK 需求列出的 error/exception/created_at 兼容字段（__post_init__
    里 error 缺省回填 message，created_at 缺省回填 timestamp）。
    """

    message: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    traceback: str | None = None
    # ── SDK 兼容字段（需求列出）────────────────────────────────────────
    error: str = ""
    exception: Exception | None = None
    created_at: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        if not self.error:
            self.error = self.message
        if self.created_at is None:
            self.created_at = self.timestamp


class Platform(abc.ABC):
    def __init__(self, config: dict, event_queue: Any = None, **kwargs) -> None:
        """平台基类构造。

        构造签名兼容 Python 本体 `__init__(config, event_queue)`，同时兼容
        旧 SDK 桩 `__init__(platform_config, platform_settings)`：第二个位置
        参数任意——若为 dict 视为 settings 而非 event_queue（内部兼容解析）；
        platform_settings/settings 也可经关键字传入。
        """
        super().__init__()
        # 平台配置（dict；兼容旧 SDK 桩的 platform_config 别名）
        self.config: dict = config if isinstance(config, dict) else {}
        self.platform_config: dict = self.config
        # 平台设置（dict；兼容旧 SDK 桩的 platform_settings 别名）
        self.settings: dict = kwargs.get("platform_settings", kwargs.get("settings", {}))
        if isinstance(event_queue, dict):
            self.settings = event_queue
            self._event_queue = None
        else:
            self._event_queue = event_queue
        self.platform_settings: dict = self.settings
        self.client_self_id: str = uuid.uuid4().hex

        # 平台运行状态
        self._status: PlatformStatus = PlatformStatus.PENDING
        self._errors: list[PlatformError] = []
        self._started_at: datetime | None = None

    # ── 状态 ────────────────────────────────────────────────────────────
    @property
    def status(self) -> PlatformStatus:
        """获取平台运行状态"""
        return self._status

    @status.setter
    def status(self, value: PlatformStatus) -> None:
        """设置平台运行状态"""
        self._status = value
        if value == PlatformStatus.RUNNING and self._started_at is None:
            self._started_at = datetime.now()

    @property
    def errors(self) -> list[PlatformError]:
        """获取错误列表"""
        return self._errors

    @property
    def last_error(self) -> PlatformError | None:
        """获取最近的错误"""
        return self._errors[-1] if self._errors else None

    # ── 平台标识（SDK 扩展字段，从 config / meta 推导）─────────────────
    @property
    def platform_id(self) -> str:
        """平台实例 ID（取 config.id，缺省回退 meta().id）。"""
        pid = self.config.get("id") if isinstance(self.config, dict) else None
        if pid:
            return str(pid)
        return self.meta().id

    @property
    def platform_name(self) -> str:
        """平台名称/类型（取 config.type，缺省回退 meta().name）。"""
        ptype = self.config.get("type") if isinstance(self.config, dict) else None
        if ptype:
            return str(ptype)
        return self.meta().name

    @property
    def platform_type(self) -> str:
        """平台类型（同 platform_name，兼容旧 SDK 桩语义）。"""
        return self.platform_name

    # ── 错误管理 ────────────────────────────────────────────────────────
    def record_error(self, message: str, traceback_str: str | None = None) -> None:
        """记录一个错误并置状态为 ERROR"""
        self._errors.append(PlatformError(message=message, traceback=traceback_str))
        self._status = PlatformStatus.ERROR

    def clear_errors(self) -> None:
        """清除错误记录；若处于 ERROR 状态则恢复为 RUNNING"""
        self._errors.clear()
        if self._status == PlatformStatus.ERROR:
            self._status = PlatformStatus.RUNNING

    def unified_webhook(self) -> bool:
        """是否正在使用统一 Webhook 模式"""
        return bool(
            self.config.get("unified_webhook_mode", False)
            and self.config.get("webhook_uuid")
        )

    def get_stats(self) -> dict:
        """获取平台统计信息（对齐原版结构）"""
        meta = self.meta()
        meta_info = {
            "id": meta.id,
            "name": meta.name,
            "display_name": meta.adapter_display_name or meta.name,
            "description": meta.description,
            "support_streaming_message": meta.support_streaming_message,
            "support_proactive_message": meta.support_proactive_message,
        }
        return {
            "id": meta.id or self.config.get("id"),
            "type": meta.name,
            "display_name": meta.adapter_display_name or meta.name,
            "status": self._status.value,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "error_count": len(self._errors),
            "last_error": {
                "message": self.last_error.message,
                "timestamp": self.last_error.timestamp.isoformat(),
                "traceback": self.last_error.traceback,
            }
            if self.last_error
            else None,
            "unified_webhook": self.unified_webhook(),
            "meta": meta_info,
        }

    # ── 运行生命周期 ────────────────────────────────────────────────────
    def run(self) -> Coroutine[Any, Any, None]:
        """得到一个平台的运行实例，需要返回一个协程对象。

        对齐原版抽象方法：基类不提供实现（抛 NotImplementedError），
        平台子类需自行实现。
        """
        raise NotImplementedError

    async def terminate(self) -> None:
        """终止一个平台的运行实例（默认空实现）。"""

    def meta(self) -> PlatformMetadata:
        """得到一个平台的元数据（默认从 config 推导，平台子类可覆盖）。

        注意：Go 宿主兼容运行时需可实例化/可调用，故给出默认实现而非
        抛 NotImplementedError（区别于原版抽象 meta）。
        """
        cfg = self.config if isinstance(self.config, dict) else {}
        return PlatformMetadata(
            name=str(cfg.get("type", "unknown")),
            description="",
            id=str(cfg.get("id", "")),
        )

    async def send_by_session(
        self,
        session: MessageSesion,
        message_chain: MessageChain,
    ) -> None:
        """通过会话发送消息（默认空实现 + 日志）。

        对齐原版签名：该方法是让插件直接通过可持久化的会话数据发送消息。
        Go 宿主兼容运行时：主动发送经宿主桥处理，本基类不实现。
        """
        logger.debug(f"send_by_session 未实现（session={session}）")
        asyncio.create_task(
            Metric.upload(msg_event_tick=1, adapter_name=self.meta().name),
        )

    def commit_event(self, event: AstrMessageEvent) -> None:
        """提交一个事件到事件队列（事件队列未就绪时丢弃并告警）。"""
        if self._event_queue is None:
            logger.warning("commit_event: 事件队列未就绪，事件被丢弃")
            return
        self._event_queue.put_nowait(event)

    def create_event(self, message: AstrBotMessage) -> AstrMessageEvent:
        """为当前平台包装消息为 AstrMessageEvent（对齐原版实现）。"""
        return AstrMessageEvent(
            message_str=message.message_str,
            message_obj=message,
            platform_meta=self.meta(),
            session_id=message.session_id,
        )

    def get_client(self) -> object:
        """获取平台的客户端对象（默认返回 None，平台子类可覆盖）。"""
        return None

    async def webhook_callback(self, request: Any = None) -> Any:
        """统一 Webhook 回调入口（默认空实现）。

        对齐原版签名。Go 宿主兼容运行时：不抛 NotImplementedError（统一
        Webhook 由 Go 侧平台适配器处理），仅记录日志并返回 None。
        """
        logger.debug("webhook_callback 未实现（平台未启用统一 Webhook）")
        return None
