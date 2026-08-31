"""utils.cron_manager CronJobManager 公开方法面对齐本体单测。

本体（astrbot.core.cron.manager.CronJobManager）公开面：
add_basic_job / add_active_job / update_job / delete_job / list_jobs /
get_next_run_time / run_job_now / sync_from_db / CronJobSchedulingError，
其中 delete_job / list_jobs / add_* / update_job / run_job_now /
sync_from_db 为 async（原 SDK 为同步同名方法，`await` 会 TypeError）。

覆盖：
- CronJobSchedulingError 异常类存在
- 方法存在且 async 形态与本体一致
- add_basic_job 返回带 job_id/name/job_type 的任务记录并可 list_jobs 查到
- delete_job 后 list_jobs 为空；update_job 对不存在任务返回 None
- get_next_run_time 对未登记任务返回 None
"""
import asyncio
import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from astrbot.core.utils.cron_manager import (  # noqa: E402
    CronJobManager,
    CronJobSchedulingError,
)

_ASYNC_METHODS = (
    "start",
    "shutdown",
    "sync_from_db",
    "add_basic_job",
    "add_active_job",
    "update_job",
    "delete_job",
    "list_jobs",
    "run_job_now",
)


class TestCronManagerSurface(unittest.IsolatedAsyncioTestCase):
    def test_scheduling_error_exists(self):
        self.assertTrue(issubclass(CronJobSchedulingError, Exception))

    def test_async_method_surface_matches_baseline(self):
        for name in _ASYNC_METHODS:
            method = getattr(CronJobManager, name, None)
            self.assertIsNotNone(method, f"CronJobManager.{name} 缺失")
            self.assertTrue(
                inspect.iscoroutinefunction(method),
                f"CronJobManager.{name} 应为 async（对齐本体）",
            )

    def test_get_next_run_time_is_sync(self):
        # 本体 get_next_run_time 为同步方法
        self.assertFalse(inspect.iscoroutinefunction(CronJobManager.get_next_run_time))

    def test_add_basic_job_signature_is_keyword_only(self):
        import inspect

        params = inspect.signature(CronJobManager.add_basic_job).parameters
        for name in ("name", "cron_expression", "handler", "payload", "persistent"):
            self.assertIn(name, params)
            self.assertEqual(
                params[name].kind,
                inspect.Parameter.KEYWORD_ONLY,
                f"add_basic_job.{name} 应为 keyword-only（对齐本体）",
            )

    async def test_add_list_delete_job_lifecycle(self):
        mgr = CronJobManager()
        called = []

        async def handler():
            called.append(1)

        record = await mgr.add_basic_job(
            name="每日报时",
            cron_expression="* * * * *",
            handler=handler,
            payload={"x": 1},
        )
        self.assertTrue(record.job_id)
        self.assertEqual(record.job_type, "basic")
        self.assertEqual(record.name, "每日报时")

        jobs = await mgr.list_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].job_id, record.job_id)

        # 按类型过滤
        self.assertEqual(await mgr.list_jobs(job_type="active_agent"), [])
        self.assertEqual(len(await mgr.list_jobs(job_type="basic")), 1)

        await mgr.delete_job(record.job_id)
        self.assertEqual(await mgr.list_jobs(), [])

    async def test_update_job_missing_returns_none(self):
        mgr = CronJobManager()
        self.assertIsNone(await mgr.update_job("no_such_job", enabled=False))

    async def test_get_next_run_time_unknown_job_returns_none(self):
        mgr = CronJobManager()
        self.assertIsNone(mgr.get_next_run_time("no_such_job"))

    async def test_sync_from_db_noop(self):
        mgr = CronJobManager()
        self.assertIsNone(await mgr.sync_from_db())

    async def test_run_job_now_executes_handler(self):
        mgr = CronJobManager()
        called = []

        async def handler():
            called.append(1)

        record = await mgr.add_basic_job(
            name="immediate",
            cron_expression="* * * * *",
            handler=handler,
        )
        await mgr.run_job_now(record.job_id)
        self.assertEqual(called, [1])
        self.assertEqual(record.status, "completed")

    async def test_add_active_job_registers_and_run_job_now_is_safe(self):
        mgr = CronJobManager()
        record = await mgr.add_active_job(
            name="主动任务",
            cron_expression="* * * * *",
            payload={"session": "aiocron:session"},
        )
        self.assertEqual(record.job_type, "active_agent")
        # SDK 运行时无法唤醒主 agent：run_job_now 不应抛异常
        await mgr.run_job_now(record.job_id)


if __name__ == "__main__":
    unittest.main()
