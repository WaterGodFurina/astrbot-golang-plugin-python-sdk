"""定时任务管理器（Go 宿主兼容运行时）。

Go 宿主没有 cron RPC，因此 .scheduler 直接暴露一个 apscheduler 的
AsyncIOScheduler（与 Python 原版 CronJobManager 一致）；若运行环境未安装
apscheduler，则降级为内置的最小调度器（线程 + 简化 cron/interval 触发），
保证插件 `context.cron_manager.scheduler` 的 add_job / get_job /
remove_job 路径永不 AttributeError。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Callable

logger = logging.getLogger("astrbot")


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
    """定时任务管理器占位（Go 宿主兼容运行时）。

    对齐 Python 原版 astrbot.core.cron.manager.CronJobManager：暴露
    `.scheduler`（apscheduler 风格）供插件直接使用；插件调用点
    `context.cron_manager.scheduler` 的 add_job / get_job / remove_job
    均可用。Go 宿主无 cron RPC，任务仅在当前 Python 子进程内生效。
    """

    def __init__(self) -> None:
        """初始化调度器（优先 apscheduler AsyncIOScheduler，缺则降级）。"""
        self.scheduler = self._create_scheduler()

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

    async def start(self) -> None:
        """启动调度器（AsyncIOScheduler 需在事件循环内调用）。"""
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

    # ── 轻量代理：对齐原版 CronJobManager 的常用方法名 ──────────────────
    def get_job(self, job_id: str):
        """按 ID 获取任务（未找到返回 None）。"""
        try:
            return self.scheduler.get_job(job_id)
        except Exception as e:
            logger.warning(f"cron_manager get_job({job_id}) 失败: {e}")
            return None

    def delete_job(self, job_id: str) -> None:
        """按 ID 删除任务。"""
        try:
            self.scheduler.remove_job(job_id)
        except Exception as e:
            logger.warning(f"cron_manager delete_job({job_id}) 失败: {e}")

    def list_jobs(self, job_type: str | None = None) -> list:
        """列出全部任务（job_type 参数保留，兼容原版签名）。"""
        try:
            return list(self.scheduler.get_jobs())
        except Exception as e:
            logger.warning(f"cron_manager list_jobs 失败: {e}")
            return []
