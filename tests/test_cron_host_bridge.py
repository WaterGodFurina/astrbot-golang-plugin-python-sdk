"""cron_manager.py 宿主桥转发（CronCreate/Update/Delete/List/RunNow）的单测。

覆盖点：
- add_basic_job：转发宿主 cron_create（job_type=cron、payload 透传）、
  宿主路径不本地调度、handler 保留映射、宿主异常/不可用回退本地；
- add_active_job：run_once → job_type=once + run_at RFC3339；
- update_job：宿主部分更新路径 / 本地回退路径；
- delete_job：转发宿主 + 本地镜像清理；
- list_jobs：宿主 Job 快照 dict → _CronJobRecord（字段对齐
  job_id/name/job_type/cron_expression/payload/enabled/next_run_time）、
  宿主不可用回退本地列表；
- run_job_now：转发宿主 / 本地回退执行 handler；
- get_next_run_time：宿主 next_run_time（RFC3339/Unix 秒）解析 / 本地回退。

运行：python3 tests/test_cron_host_bridge.py
"""
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _BridgeTestCase(unittest.IsolatedAsyncioTestCase):
    """提供 patch astrbot.core.star.context.get_host_bridge 的基类
    （FakeBridge 注入模式对齐 tests/test_skill_manager_alignment.py）。"""

    def _fake_bridge(self, **overrides):
        methods = {
            "ensure_connected": lambda self: True,
        }
        methods.update(overrides)
        return type("FakeBridge", (), methods)()

    def _patch_bridge(self, fake):
        import astrbot.core.star.context as ctx_mod

        old = ctx_mod.get_host_bridge
        ctx_mod.get_host_bridge = lambda: fake
        self.addCleanup(lambda: setattr(ctx_mod, "get_host_bridge", old))


def _mgr():
    from astrbot.core.utils.cron_manager import CronJobManager

    return CronJobManager()


def _host_job(**extra):
    """宿主 Job 快照 dict（字段对齐宿主 Job JSON）。"""
    job = {
        "job_id": "h1",
        "name": "n",
        "job_type": "cron",
        "cron_expression": "* * * * *",
        "payload": {},
        "enabled": True,
        "next_run_time": "2026-09-01T00:00:00+00:00",
    }
    job.update(extra)
    return job


class TestAddJobHostBridge(_BridgeTestCase):
    """add_basic_job / add_active_job 的宿主转发与本地回退。"""

    async def test_add_basic_job_forwards_to_host(self):
        calls = {}

        def cron_create(self, name="", job_type="", cron_expression="", timezone="",
                        payload=None, description="", enabled=True,
                        run_once=False, run_at=""):
            calls.update(name=name, job_type=job_type,
                         cron_expression=cron_expression, payload=payload,
                         enabled=enabled)
            return _host_job(name=name, cron_expression=cron_expression,
                             payload=dict(payload or {}))

        self._patch_bridge(self._fake_bridge(cron_create=cron_create))
        mgr = _mgr()
        handler = lambda: None  # noqa: E731
        record = await mgr.add_basic_job(
            name="n", cron_expression="*/5 * * * *", handler=handler,
            payload={"a": 1},
        )
        self.assertEqual(record.job_id, "h1")
        self.assertEqual(calls["job_type"], "cron")
        self.assertEqual(calls["cron_expression"], "*/5 * * * *")
        self.assertEqual(calls["payload"], {"a": 1})
        self.assertTrue(calls["enabled"])
        self.assertIn("h1", mgr._host_jobs)
        self.assertIs(mgr._basic_handlers["h1"], handler)
        # 宿主路径不做本地调度
        self.assertIsNone(mgr.scheduler.get_job("h1"))

    async def test_add_active_job_run_once_maps_to_once_type(self):
        calls = {}

        def cron_create(self, **kwargs):
            calls.update(kwargs)
            return _host_job(job_id="h2", job_type="once", run_once=True)

        self._patch_bridge(self._fake_bridge(cron_create=cron_create))
        record = await _mgr().add_active_job(
            name="a", cron_expression=None, run_once=True,
            run_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(record.job_id, "h2")
        self.assertEqual(calls["job_type"], "once")
        self.assertTrue(calls["run_once"])
        self.assertEqual(calls["run_at"], "2026-09-01T12:00:00+00:00")

    async def test_add_basic_job_falls_back_local_on_host_error(self):
        """宿主 RPC 抛异常 → 回退本地调度器实现（兜底保留）。"""
        def cron_create(self, **kwargs):
            raise RuntimeError("host down")

        self._patch_bridge(self._fake_bridge(cron_create=cron_create))
        mgr = _mgr()
        record = await mgr.add_basic_job(
            name="n", cron_expression="*/5 * * * *", handler=lambda: None
        )
        self.assertIsNotNone(record.job_id)
        self.assertNotIn(record.job_id, mgr._host_jobs)
        self.assertIn(record.job_id, mgr._jobs)

    async def test_add_basic_job_falls_back_local_when_bridge_absent(self):
        self._patch_bridge(None)
        mgr = _mgr()
        record = await mgr.add_basic_job(
            name="n", cron_expression="*/5 * * * *", handler=lambda: None
        )
        self.assertEqual(record.name, "n")
        self.assertNotIn(record.job_id, mgr._host_jobs)

    async def test_add_basic_job_falls_back_on_empty_snapshot(self):
        """宿主返回空快照（创建失败）→ 回退本地。"""
        self._patch_bridge(
            self._fake_bridge(cron_create=lambda self, **kwargs: {})
        )
        mgr = _mgr()
        record = await mgr.add_basic_job(
            name="n", cron_expression="*/5 * * * *", handler=lambda: None
        )
        self.assertNotIn(record.job_id, mgr._host_jobs)


class TestUpdateDeleteHostBridge(_BridgeTestCase):
    """update_job / delete_job 的宿主转发与本地回退。"""

    async def test_update_job_host_path(self):
        calls = {}

        def cron_update(self, job_id="", fields=None):
            calls.update(job_id=job_id, fields=dict(fields or {}))
            job = _host_job(job_id=job_id)
            job.update(fields or {})
            return job

        self._patch_bridge(self._fake_bridge(cron_update=cron_update))
        mgr = _mgr()
        mgr._jobs["h1"] = _mk_record("h1")
        mgr._host_jobs.add("h1")
        record = await mgr.update_job("h1", name="new-name", enabled=False)
        self.assertEqual(record.name, "new-name")
        self.assertFalse(record.enabled)
        self.assertEqual(calls["fields"], {"name": "new-name", "enabled": False})

    async def test_update_job_local_fallback_when_host_absent(self):
        self._patch_bridge(None)
        mgr = _mgr()
        record = await mgr.add_basic_job(
            name="n", cron_expression="*/5 * * * *", handler=lambda: None
        )
        upd = await mgr.update_job(record.job_id, name="m")
        self.assertEqual(upd.name, "m")

    async def test_update_job_local_fallback_when_host_snapshot_empty(self):
        """宿主不认识该任务（返回空快照）→ 回退本地更新。"""
        self._patch_bridge(
            self._fake_bridge(cron_update=lambda self, job_id="", fields=None: {})
        )
        mgr = _mgr()
        mgr._jobs["local1"] = _mk_record("local1")
        upd = await mgr.update_job("local1", name="m")
        self.assertEqual(upd.name, "m")

    async def test_update_job_missing_returns_none(self):
        self._patch_bridge(None)
        self.assertIsNone(await _mgr().update_job("ghost", name="x"))

    async def test_delete_job_forwards_and_cleans_local_mirror(self):
        calls = {}

        def cron_delete(self, job_id=""):
            calls["job_id"] = job_id
            return True

        self._patch_bridge(self._fake_bridge(cron_delete=cron_delete))
        mgr = _mgr()
        mgr._jobs["h1"] = _mk_record("h1")
        mgr._host_jobs.add("h1")
        mgr._basic_handlers["h1"] = lambda: None
        await mgr.delete_job("h1")
        self.assertEqual(calls["job_id"], "h1")
        self.assertNotIn("h1", mgr._jobs)
        self.assertNotIn("h1", mgr._host_jobs)
        self.assertNotIn("h1", mgr._basic_handlers)

    async def test_delete_job_host_unavailable_still_cleans_local(self):
        self._patch_bridge(None)
        mgr = _mgr()
        record = await mgr.add_basic_job(
            name="n", cron_expression="*/5 * * * *", handler=lambda: None
        )
        await mgr.delete_job(record.job_id)
        self.assertNotIn(record.job_id, mgr._jobs)


class TestListJobsHostBridge(_BridgeTestCase):
    """list_jobs：宿主快照解析 / 本地回退。"""

    async def test_list_jobs_parses_host_job_json(self):
        self._patch_bridge(
            self._fake_bridge(
                cron_list=lambda self, job_type="": [
                    _host_job(
                        job_id="h1", name="n1", payload={"x": 1},
                        next_run_time=1790000000,
                    ),
                    _host_job(job_id="", name="invalid"),  # 无 job_id：跳过
                    "bad-entry",  # 非 dict：跳过
                ]
            )
        )
        records = await _mgr().list_jobs()
        self.assertEqual(len(records), 1)
        r = records[0]
        # 字段对齐宿主 Job JSON
        self.assertEqual(r.job_id, "h1")
        self.assertEqual(r.name, "n1")
        self.assertEqual(r.job_type, "cron")
        self.assertEqual(r.cron_expression, "* * * * *")
        self.assertEqual(r.payload, {"x": 1})
        self.assertTrue(r.enabled)
        self.assertIsInstance(r.next_run_time, datetime)
        self.assertEqual(r.next_run_time.tzinfo, timezone.utc)

    async def test_list_jobs_passes_job_type_filter(self):
        calls = {}

        def cron_list(self, job_type=""):
            calls["job_type"] = job_type
            return []

        self._patch_bridge(self._fake_bridge(cron_list=cron_list))
        await _mgr().list_jobs(job_type="once")
        self.assertEqual(calls["job_type"], "once")

    async def test_list_jobs_falls_back_local_when_bridge_absent(self):
        self._patch_bridge(None)
        mgr = _mgr()
        rec = await mgr.add_basic_job(
            name="n", cron_expression="*/5 * * * *", handler=lambda: None
        )
        jobs = await mgr.list_jobs()
        self.assertIn(rec.job_id, [j.job_id for j in jobs])

    async def test_list_jobs_falls_back_local_on_rpc_error(self):
        def cron_list(self, job_type=""):
            raise RuntimeError("host down")

        self._patch_bridge(self._fake_bridge(cron_list=cron_list))
        mgr = _mgr()
        rec = await mgr.add_basic_job(
            name="n", cron_expression="*/5 * * * *", handler=lambda: None
        )
        jobs = await mgr.list_jobs()
        self.assertIn(rec.job_id, [j.job_id for j in jobs])


class TestRunNowAndGetNextRunTime(_BridgeTestCase):
    """run_job_now / get_next_run_time 的宿主转发与本地回退。"""

    async def test_run_job_now_forwards_to_host(self):
        calls = {}

        def cron_run_now(self, job_id=""):
            calls["job_id"] = job_id
            return True

        self._patch_bridge(self._fake_bridge(cron_run_now=cron_run_now))
        mgr = _mgr()
        mgr._host_jobs.add("h1")
        await mgr.run_job_now("h1")
        self.assertEqual(calls["job_id"], "h1")

    async def test_run_job_now_falls_back_local_and_executes_handler(self):
        self._patch_bridge(None)
        mgr = _mgr()
        ran = []

        async def handler():
            ran.append(1)

        rec = await mgr.add_basic_job(
            name="n", cron_expression="*/5 * * * *", handler=handler
        )
        await mgr.run_job_now(rec.job_id)
        self.assertEqual(ran, [1])

    async def test_run_job_now_host_absent_warns_for_unknown_job(self):
        self._patch_bridge(None)
        await _mgr().run_job_now("ghost")  # 不抛即通过

    def test_get_next_run_time_from_host_rfc3339(self):
        self._patch_bridge(
            self._fake_bridge(
                cron_list=lambda self, job_type="": [
                    _host_job(next_run_time="2026-09-01T00:00:00+00:00")
                ]
            )
        )
        dt = _mgr().get_next_run_time("h1")
        self.assertIsInstance(dt, datetime)
        self.assertEqual(dt.tzinfo, timezone.utc)
        self.assertEqual(dt.year, 2026)

    def test_get_next_run_time_from_host_unix_seconds(self):
        self._patch_bridge(
            self._fake_bridge(
                cron_list=lambda self, job_type="": [
                    _host_job(next_run_time=1790000000)
                ]
            )
        )
        dt = _mgr().get_next_run_time("h1")
        self.assertIsInstance(dt, datetime)
        self.assertEqual(dt.timestamp(), 1790000000.0)

    def test_get_next_run_time_falls_back_local(self):
        """宿主不可用 → 回退本地调度器读取。"""
        from astrbot.core.utils.cron_manager import _SimpleCronTrigger

        self._patch_bridge(None)
        mgr = _mgr()
        mgr.scheduler.add_job(
            lambda: None, trigger=_SimpleCronTrigger(hour=7, minute=30), id="j1"
        )
        dt = mgr.get_next_run_time("j1")
        self.assertIsInstance(dt, datetime)

    def test_get_next_run_time_unknown_returns_none(self):
        self._patch_bridge(None)
        self.assertIsNone(_mgr().get_next_run_time("ghost"))


def _mk_record(job_id: str):
    from astrbot.core.utils.cron_manager import _CronJobRecord

    return _CronJobRecord(
        job_id=job_id,
        name="n",
        job_type="basic",
        cron_expression="*/5 * * * *",
    )


if __name__ == "__main__":
    unittest.main()
