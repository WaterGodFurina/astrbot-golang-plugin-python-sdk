"""定时任务管理器（Go 宿主兼容运行时，对齐本体 core.cron.manager 公开面）。

Go 宿主没有插件侧 cron RPC，因此 .scheduler 直接暴露一个 apscheduler 的
AsyncIOScheduler（与 Python 本体 CronJobManager 一致）；若运行环境未安装
apscheduler，则降级为内置的最小调度器（线程 + 简化 cron/interval 触发），
保证插件 `context.cron_manager.scheduler` 的 add_job / get_job /
remove_job 路径永不 AttributeError。

同时对齐本体 `astrbot.core.cron.manager.CronJobManager` 的公开方法面
（add_basic_job / add_active_job / update_job / delete_job / list_jobs /
get_next_run_time / run_job_now / sync_from_db / CronJobSchedulingError）：
SDK 运行时无数据库与主 agent 唤醒能力，任务记录保存在本进程内存
（_CronJobRecord），active_agent 任务触发时仅记录日志。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:  # pragma: no cover
    from astrbot.core.star.context import Context

logger = logging.getLogger("astrbot")


class CronJobSchedulingError(Exception):
    """定时任务登记失败时抛出（对齐本体 CronJobSchedulingError）。"""


@dataclass
class _CronJobRecord:
    """内存定时任务记录（SDK 无 DB，属性面对齐本体 CronJob PO）。"""

    job_id: str
    name: str
    job_type: str = "basic"
    cron_expression: str | None = None
    timezone: str | None = None
    payload: dict = field(default_factory=dict)
    description: str | None = None
    enabled: bool = True
    persistent: bool = False
    run_once: bool = False
    run_at: datetime | None = None
    next_run_time: datetime | None = None
    status: str = "pending"
    last_run_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        """补齐创建时间。"""
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)


@dataclass
class _SimpleCronTrigger:
    """轻量 cron 触发器（仅降级调度器识别）：字段为字面量时填 int，通配为 None。"""

    hour: int | None = None
    minute: int | None = None


class _MinimalScheduler:
    """最小内存调度器（无 apscheduler 时的降级实现）。

    支持 apscheduler 风格的 add_job / get_job / remove_job / start /
    shutdown。trigger 可为 apscheduler 的 CronTrigger / IntervalTrigger
    （以属性探测方式读取），也可为带 hour/minute（或 interval 秒数）的
    简单对象。任务函数若为 async，则在独立事件循环中执行。
    """

    def __init__(self) -> None:
        """初始化最小调度器。"""
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._auto_seq = 0

    @property
    def running(self) -> bool:
        """是否已启动。"""
        return self._running

    def _parse_trigger(self, trigger) -> tuple[str, Any] | None:
        """从 trigger 对象提取 (类型, 参数)。"""
        if trigger is None:
            return None
        # CronTrigger：探测 hour / minute 属性
        hour = getattr(trigger, "hour", None)
        minute = getattr(trigger, "minute", None)
        if hour is not None and minute is not None:
            try:
                return ("cron", (int(hour), int(minute)))
            except (TypeError, ValueError):
                pass
        # IntervalTrigger：探测 interval 属性
        interval = getattr(trigger, "interval", None)
        if interval is not None:
            seconds = getattr(interval, "total_seconds", None)
            if callable(seconds):
                return ("interval", max(1.0, float(seconds())))
        # 带 interval_seconds 的简单对象（本类 add_job 的宽松入参）
        secs = getattr(trigger, "interval_seconds", None)
        if secs is not None:
            return ("interval", max(1.0, float(secs)))
        return None

    def _next_run(self, trigger_kind: str, trigger_arg: Any, now: float) -> float:
        """计算下一次触发时间戳。"""
        if trigger_kind == "interval":
            return now + float(trigger_arg)
        # cron：按当天 hour:minute 计算，已过则顺延到明天
        hour, minute = trigger_arg
        t = time.localtime(now)
        candidate = int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hour, minute, 0, t.tm_wday, t.tm_yday, t.tm_isdst)))
        if candidate <= now:
            candidate += 86400
        return float(candidate)

    def add_job(
        self,
        func: Callable,
        trigger=None,
        id: str | None = None,
        replace_existing: bool = False,
        misfire_grace_time: int | None = None,
        **kwargs,
    ):
        """登记一个定时任务（返回 job 占位 dict，对齐 apscheduler 风格）。"""
        with self._lock:
            # 自增序列生成 ID：避免按 len(_jobs) 生成导致任务删除后
            # ID 冲突（新旧任务同名覆盖）
            self._auto_seq += 1
            job_id = id or f"job_{self._auto_seq}"
            if job_id in self._jobs and not replace_existing:
                return self._jobs[job_id]
            parsed = self._parse_trigger(trigger)
            now = time.time()
            if parsed is None:
                # 无法解析的触发器：登记但不实际调度（不崩即可）
                job = {
                    "id": job_id,
                    "func": func,
                    "next_run": None,
                    "kind": None,
                    "arg": None,
                }
            else:
                kind, arg = parsed
                job = {
                    "id": job_id,
                    "func": func,
                    "next_run": self._next_run(kind, arg, now),
                    "kind": kind,
                    "arg": arg,
                }
            self._jobs[job_id] = job
        if parsed is not None and not self._running:
            self.start()
        logger.info(f"cron_manager(降级调度器) 已登记任务: {job_id}")
        return job

    def get_job(self, job_id: str):
        """按 ID 获取任务（未找到返回 None）。"""
        with self._lock:
            return self._jobs.get(job_id)

    def remove_job(self, job_id: str) -> None:
        """按 ID 移除任务。"""
        with self._lock:
            self._jobs.pop(job_id, None)

    def _run_func(self, func: Callable) -> None:
        """执行任务函数（async 用独立事件循环，同步直接调用）。"""
        try:
            if asyncio.iscoroutinefunction(func):
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(func())
                finally:
                    loop.close()
            else:
                func()
        except Exception as e:
            logger.warning(f"cron_manager(降级调度器) 任务执行失败: {e}")

    def _runner(self) -> None:
        """后台线程主循环：每 5 秒扫描一次到期任务。"""
        while not self._stop_event.is_set():
            self._stop_event.wait(5)
            if self._stop_event.is_set():
                break
            now = time.time()
            due = []
            with self._lock:
                for job_id, job in self._jobs.items():
                    if job.get("next_run") is not None and job["next_run"] <= now:
                        # 计算下次触发时间（interval/cron 都按触发时刻顺延）
                        if job.get("kind") and job.get("arg") is not None:
                            job["next_run"] = self._next_run(job["kind"], job["arg"], now)
                        else:
                            job["next_run"] = None
                        due.append(job)
            for job in due:
                t = threading.Thread(target=self._run_func, args=(job["func"],), daemon=True)
                t.start()

    def start(self) -> None:
        """启动调度线程（幂等）。"""
        if self._running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._runner, daemon=True, name="minimal-scheduler")
        self._running = True
        self._thread.start()

    def shutdown(self, wait: bool = False) -> None:
        """停止调度线程（幂等）。"""
        if not self._running:
            return
        self._stop_event.set()
        self._running = False
        if wait and self._thread is not None:
            self._thread.join(timeout=5)


class _AutoStartSchedulerProxy:
    """apscheduler 调度器的懒启动代理。

    插件的 schedule_jobs() 是同步代码，不会主动调用 scheduler.start()；
    代理在首次 add_job 时自动启动底层 AsyncIOScheduler，其余属性/方法
    原样转发，保证 .scheduler 使用体验与原生 apscheduler 一致。
    """

    def __init__(self, inner) -> None:
        """包装底层调度器。"""
        self._inner = inner
        self._start_attempted = False

    def _ensure_started(self) -> None:
        """懒启动：仅在首次 add_job 时尝试一次。"""
        if self._start_attempted:
            return
        self._start_attempted = True
        try:
            if not self._inner.running:
                self._inner.start()
                logger.info("cron_manager 调度器已懒启动")
        except Exception as e:
            # AsyncIOScheduler.start() 必须在事件循环线程调用；插件的
            # schedule_jobs() 是同步代码，常在 __init__（gRPC/主线程，无运行
            # 循环）里执行——失败后重置标记，允许在事件循环上下文中重试，
            # 否则定时任务静默不触发。
            self._start_attempted = False
            logger.warning(
                f"cron_manager 调度器懒启动失败（将在事件循环上下文重试）: {e}"
            )

    def add_job(self, *args, **kwargs):
        """登记任务（触发懒启动），对齐 apscheduler 签名。"""
        self._ensure_started()
        if not self._inner.running:
            # 已在事件循环上下文内（async handler / loop 任务）：直接启动。
            # 首次 add_job 若发生在无运行循环的线程，此处也顺带重试一次。
            try:
                try:
                    asyncio.get_running_loop()
                    in_loop = True
                except RuntimeError:
                    in_loop = False
                if in_loop:
                    self._start_attempted = True
                    self._inner.start()
            except Exception as e:
                logger.debug(f"cron_manager 调度器启动重试失败: {e}")
        return self._inner.add_job(*args, **kwargs)

    def __getattr__(self, name):
        """其余属性/方法（running / get_job / remove_job / shutdown ...）转发。"""
        return getattr(self._inner, name)


class CronJobManager:
    """定时任务管理器（Go 宿主兼容运行时）。

    对齐本体 astrbot.core.cron.manager.CronJobManager 的公开方法面：暴露
    `.scheduler`（apscheduler 风格）供插件直接使用，同时提供 add_basic_job /
    add_active_job / update_job / delete_job / list_jobs / get_next_run_time /
    run_job_now / sync_from_db。SDK 运行时无 DB 与主 agent，任务仅在本进程
    内存生效；本体 __init__(db) 的 db 参数保留为可选（SDK 忽略）。
    """

    def __init__(self, db: Any | None = None) -> None:
        """初始化调度器（优先 apscheduler AsyncIOScheduler，缺则降级）。"""
        self.db = db  # SDK 无 DB 设施：仅占位，对齐本体构造签名
        self.scheduler = self._create_scheduler()
        self._basic_handlers: dict[str, Callable[..., Any]] = {}
        self._jobs: dict[str, _CronJobRecord] = {}

    def _create_scheduler(self):
        """创建调度器：优先 AsyncIOScheduler，失败则降级为最小调度器。"""
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler

            scheduler = AsyncIOScheduler()
            logger.info("cron_manager 使用 apscheduler AsyncIOScheduler")
            return _AutoStartSchedulerProxy(scheduler)
        except Exception as e:
            logger.warning(f"apscheduler 不可用，cron_manager 降级为最小调度器: {e}")
            return _MinimalScheduler()

    async def start(self, ctx: "Context | None" = None) -> None:
        """启动调度器（AsyncIOScheduler 需在事件循环内调用）。"""
        if ctx is not None:  # 对齐本体 start(ctx)：SDK 不使用 Context
            self.ctx = ctx
        try:
            if not getattr(self.scheduler, "running", False):
                self.scheduler.start()
        except Exception as e:
            logger.warning(f"cron_manager start 失败（任务将在首次 add_job 时重试）: {e}")

    async def shutdown(self) -> None:
        """关闭调度器（插件卸载时调用）。"""
        try:
            if getattr(self.scheduler, "running", False):
                self.scheduler.shutdown(wait=False)
        except Exception as e:
            logger.warning(f"cron_manager shutdown 失败: {e}")

    # ── 对齐本体 core.cron.manager.CronJobManager 的公开方法面 ─────────────

    async def sync_from_db(self) -> None:
        """从数据库恢复持久化任务（SDK 无 DB 设施：no-op 降级）。"""
        return None

    async def add_basic_job(
        self,
        *,
        name: str,
        cron_expression: str,
        handler: Callable[..., Any],
        description: str | None = None,
        timezone: str | None = None,
        payload: dict | None = None,
        enabled: bool = True,
        persistent: bool = False,
    ) -> _CronJobRecord:
        """登记一个 basic 定时任务（对齐本体 add_basic_job 关键字签名）。

        本体把任务写入 DB 并返回 CronJob PO；SDK 无 DB，任务记录保存在
        内存（_CronJobRecord，属性面对齐 CronJob PO），调度经 .scheduler
        （apscheduler 或降级调度器）执行 handler。
        """
        record = _CronJobRecord(
            job_id=uuid.uuid4().hex,
            name=name,
            job_type="basic",
            cron_expression=cron_expression,
            timezone=timezone,
            payload=dict(payload or {}),
            description=description,
            enabled=enabled,
            persistent=persistent,
        )
        self._jobs[record.job_id] = record
        self._basic_handlers[record.job_id] = handler
        if enabled:
            try:
                self._schedule_job(record)
            except CronJobSchedulingError as e:
                logger.warning(f"cron_manager add_basic_job({name!r}) 调度失败: {e}")
        return record

    async def add_active_job(
        self,
        *,
        name: str,
        cron_expression: str | None,
        payload: dict | None = None,
        description: str | None = None,
        timezone: str | None = None,
        enabled: bool = True,
        persistent: bool = True,
        run_once: bool = False,
        run_at: datetime | None = None,
    ) -> _CronJobRecord:
        """登记一个 active_agent 定时任务（对齐本体 add_active_job 签名）。

        本体触发时会唤醒主 agent 处理 payload；SDK 运行时无主 agent，
        触发时仅记录日志（⚠️ 功能降级）。
        """
        if run_once and run_at is not None:
            payload = {**(payload or {}), "run_at": run_at.isoformat()}
        record = _CronJobRecord(
            job_id=uuid.uuid4().hex,
            name=name,
            job_type="active_agent",
            cron_expression=cron_expression,
            timezone=timezone,
            payload=dict(payload or {}),
            description=description,
            enabled=enabled,
            persistent=persistent,
            run_once=run_once,
            run_at=run_at,
        )
        self._jobs[record.job_id] = record
        if enabled:
            try:
                self._schedule_job(record)
            except CronJobSchedulingError as e:
                logger.warning(f"cron_manager add_active_job({name!r}) 调度失败: {e}")
        return record

    async def update_job(self, job_id: str, **kwargs) -> _CronJobRecord | None:
        """更新任务（对齐本体 update_job：未找到返回 None）。

        支持更新 name/cron_expression/timezone/payload/description/enabled/
        persistent/run_once/run_at 与 handler（本体 handler 由调用方另行
        维护，SDK 一并接收便于重调度）。
        """
        record = self._jobs.get(job_id)
        if record is None:
            return None
        handler = kwargs.pop("handler", None)
        for key, value in kwargs.items():
            if hasattr(record, key) and not key.startswith("_"):
                setattr(record, key, value)
        if handler is not None:
            self._basic_handlers[job_id] = handler
        self._remove_scheduled(job_id)
        if record.enabled:
            try:
                self._schedule_job(record)
            except CronJobSchedulingError as e:
                logger.warning(f"cron_manager update_job({job_id}) 调度失败: {e}")
        return record

    async def delete_job(self, job_id: str) -> None:
        """删除任务（对齐本体 delete_job：async，同时移除调度与处理器）。"""
        self._remove_scheduled(job_id)
        self._jobs.pop(job_id, None)
        self._basic_handlers.pop(job_id, None)

    async def list_jobs(self, job_type: str | None = None) -> list[_CronJobRecord]:
        """列出全部任务（对齐本体 list_jobs：async，可按 job_type 过滤）。"""
        if job_type:
            return [r for r in self._jobs.values() if r.job_type == job_type]
        return list(self._jobs.values())

    def get_next_run_time(self, job_id: str) -> datetime | None:
        """读取任务下一次运行时间（对齐本体 get_next_run_time）。

        apscheduler 任务取 next_run_time；降级调度器任务（dict 形态）取
        next_run 时间戳。返回 UTC 感知 datetime，未登记/未调度返回 None。
        """
        try:
            scheduled = self.scheduler.get_job(job_id)
        except Exception:
            return None
        if scheduled is None:
            return None
        next_run = getattr(scheduled, "next_run_time", None)
        if next_run is None and isinstance(scheduled, dict):
            next_run = scheduled.get("next_run")
        if next_run is None:
            return None
        try:
            if isinstance(next_run, (int, float)):
                return datetime.fromtimestamp(next_run, tz=timezone.utc)
            return next_run.astimezone(timezone.utc)
        except (TypeError, ValueError, OSError):
            return None

    async def run_job_now(self, job_id: str) -> None:
        """立即执行一次任务（对齐本体 run_job_now）。

        basic 任务直接调用 handler；active_agent 任务 SDK 无法唤醒宿主
        主 agent，仅记录日志。
        """
        record = self._jobs.get(job_id)
        if record is None:
            logger.warning(f"cron_manager run_job_now: 任务 {job_id} 不存在")
            return
        if record.job_type == "basic":
            await self._run_basic_job(job_id)
        else:
            logger.warning(
                f"cron_manager run_job_now: active_agent 任务 {job_id} 在 SDK "
                "运行时无法唤醒宿主主 agent，已忽略"
            )

    # ── 内部调度实现 ────────────────────────────────────────────────

    def _resolve_tzinfo(self, timezone_name: str | None):
        """把时区名解析为 tzinfo（无效时告警并回退系统时区）。"""
        if not timezone_name:
            return None
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(timezone_name)
        except Exception:
            logger.warning(
                f"cron_manager: 无效时区 {timezone_name!r}，回退系统时区"
            )
            return None

    def _build_trigger(self, record: _CronJobRecord):
        """按任务记录构造调度触发器（失败抛 CronJobSchedulingError）。"""
        try:
            if record.run_once:
                run_at = record.run_at
                if run_at is None:
                    raise ValueError("run_once job missing run_at timestamp")
                try:
                    from apscheduler.triggers.date import DateTrigger
                except ImportError:
                    return None  # 降级调度器不支持一次性触发：登记不调度
                tzinfo = self._resolve_tzinfo(record.timezone)
                if run_at.tzinfo is None and tzinfo is not None:
                    run_at = run_at.replace(tzinfo=tzinfo)
                return DateTrigger(run_date=run_at, timezone=tzinfo)

            expr = str(record.cron_expression or "").strip()
            if not expr:
                raise ValueError("recurring job missing cron_expression")
            try:
                from apscheduler.triggers.cron import CronTrigger
            except ImportError:
                CronTrigger = None
            if CronTrigger is not None:
                return CronTrigger.from_crontab(
                    expr, timezone=self._resolve_tzinfo(record.timezone)
                )
            # 降级调度器：仅支持 minute/hour 为字面量的五段 crontab
            fields = expr.split()
            if len(fields) != 5:
                raise ValueError(f"invalid crontab expression: {expr!r}")
            minute, hour = fields[0], fields[1]
            if minute.isdigit() and hour.isdigit():
                return _SimpleCronTrigger(hour=int(hour), minute=int(minute))
            return None  # 含通配/步进字段：降级调度器无法表达，登记不调度
        except (ValueError, TypeError) as e:
            raise CronJobSchedulingError(str(e)) from e

    def _schedule_job(self, record: _CronJobRecord) -> None:
        """把任务记录登记到 .scheduler（对齐本体 _schedule_job 行为）。"""
        trigger = self._build_trigger(record)
        if trigger is None:
            logger.warning(
                f"cron_manager: 任务 {record.job_id}({record.name!r}) 触发器"
                "在当前调度器下不可用，仅登记不实际调度"
            )
            return
        if record.job_type == "basic":
            job_func = self._make_basic_job_func(record.job_id)
        else:
            job_func = self._make_active_job_func(record.job_id)
        try:
            self.scheduler.add_job(
                job_func,
                trigger=trigger,
                id=record.job_id,
                replace_existing=True,
                misfire_grace_time=30,
            )
        except (ValueError, TypeError) as e:
            raise CronJobSchedulingError(str(e)) from e

    def _remove_scheduled(self, job_id: str) -> None:
        """从调度器移除任务（存在才移除，对齐本体 _remove_scheduled）。"""
        try:
            if self.scheduler.get_job(job_id) is not None:
                self.scheduler.remove_job(job_id)
        except Exception as e:
            logger.warning(f"cron_manager: 移除调度任务 {job_id} 失败: {e}")

    def _make_basic_job_func(self, job_id: str) -> Callable[[], Any]:
        """构造 basic 任务回调（闭包携带 job_id，兼容两种调度器）。"""

        async def _basic_job_func() -> None:
            await self._run_basic_job(job_id)

        return _basic_job_func

    def _make_active_job_func(self, job_id: str) -> Callable[[], Any]:
        """构造 active_agent 任务回调（SDK 无主 agent：触发仅记录日志）。"""

        async def _active_job_func() -> None:
            logger.warning(
                f"cron_manager: active_agent 任务 {job_id} 触发，但 SDK 运行时"
                "无法唤醒宿主主 agent，本次触发已忽略"
            )

        return _active_job_func

    async def _run_basic_job(self, job_id: str) -> None:
        """执行 basic 任务 handler（对齐本体 _run_basic_job 语义）。"""
        handler = self._basic_handlers.get(job_id)
        record = self._jobs.get(job_id)
        if not handler:
            logger.warning(
                f"cron_manager: 任务 {job_id} 的 handler 已丢失，跳过执行"
            )
            return
        payload = dict(record.payload) if record and record.payload else {}
        try:
            result = handler(**payload) if payload else handler()
            if asyncio.iscoroutine(result):
                await result
            if record is not None:
                record.status = "completed"
                record.last_error = None
        except Exception as e:  # noqa: BLE001
            if record is not None:
                record.status = "failed"
                record.last_error = str(e)
            logger.error(f"cron_manager 任务 {job_id} 执行失败: {e!s}")

    # ── 轻量代理：兼容既有插件对 scheduler 的直接操作 ──────────────────
    def get_job(self, job_id: str):
        """按 ID 获取调度器中的任务（未找到返回 None）。"""
        try:
            return self.scheduler.get_job(job_id)
        except Exception as e:
            logger.warning(f"cron_manager get_job({job_id}) 失败: {e}")
            return None
