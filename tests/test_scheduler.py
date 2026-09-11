"""
統一排程器。

Phase 1 修掉的問題:原本有三套互不知道對方存在的排程迴圈,
開倉路徑三條、平倉路徑兩條。現在收斂成一個 tick 迴圈 + 可組態的任務子集。

這裡也鎖住「每個生產 service 維持原本職責範圍」——
切換到統一排程器不該讓每個 process 突然去做全部的事。
"""
import unittest
from unittest.mock import MagicMock

from agmcis.scheduling.jobs import Job
from agmcis.scheduling.runner import (
    JOB_SET_ALL,
    JOB_SET_OPPORTUNITY,
    JOB_SET_POSITION,
    JOB_SET_TRADER,
    JOB_SETS,
    SchedulerRunner,
)


def job(name="j", interval=60, run=None):
    return Job(name=name, run=run or (lambda: {"ok": True}), interval_seconds=interval)


class TestJobScheduling(unittest.TestCase):

    def test_new_job_is_due_immediately(self):
        self.assertTrue(job().is_due(now=0))

    def test_not_due_before_interval_elapses(self):
        j = job(interval=60)
        j.mark_success({}, now=100)
        self.assertFalse(j.is_due(now=159))
        self.assertTrue(j.is_due(now=160))

    def test_disabled_job_is_never_due(self):
        j = job()
        j.enabled = False
        self.assertFalse(j.is_due(now=0))
        self.assertIsNone(j.seconds_until_due(now=0))

    def test_failure_still_advances_the_clock(self):
        """失敗的任務不該在每個 tick 重試,否則會把 log 灌爆。"""
        j = job(interval=60)
        j.mark_failure(RuntimeError("boom"), now=100)
        self.assertFalse(j.is_due(now=120))
        self.assertEqual(j.error_count, 1)

    def test_counters(self):
        j = job()
        j.mark_success({}, now=1)
        j.mark_success({}, now=2)
        j.mark_failure(RuntimeError("x"), now=3)
        self.assertEqual(j.run_count, 2)
        self.assertEqual(j.error_count, 1)
        self.assertEqual(j.last_error, "x")


class TestRunnerTick(unittest.TestCase):

    def setUp(self):
        self.logger = MagicMock()

    def _runner(self, jobs, tmpfile="/dev/null"):
        return SchedulerRunner(jobs, status_file=tmpfile, logger=self.logger)

    def test_runs_due_jobs_only(self):
        fast = MagicMock(return_value={"ok": 1})
        slow = MagicMock(return_value={"ok": 2})

        jobs = [job("fast", 10, fast), job("slow", 1000, slow)]
        runner = self._runner(jobs)

        runner.tick(now=0)          # 兩者都是第一次,都會跑
        self.assertEqual(fast.call_count, 1)
        self.assertEqual(slow.call_count, 1)

        runner.tick(now=20)         # 只有 fast 到期
        self.assertEqual(fast.call_count, 2)
        self.assertEqual(slow.call_count, 1)

    def test_one_failing_job_does_not_stop_the_others(self):
        boom = MagicMock(side_effect=RuntimeError("db down"))
        fine = MagicMock(return_value={"ok": True})

        runner = self._runner([job("boom", 10, boom), job("fine", 10, fine)])
        ran = runner.tick(now=0)

        self.assertEqual(fine.call_count, 1)
        self.assertIn("error", ran["boom"])
        self.assertTrue(self.logger.exception.called)

    def test_status_reports_each_job(self):
        runner = self._runner([job("a", 10), job("b", 20)])
        runner.tick(now=0)
        status = runner.status()

        names = [j["name"] for j in status["jobs"]]
        self.assertEqual(names, ["a", "b"])
        self.assertEqual(status["jobs"][0]["runs"], 1)

    def test_status_surfaces_real_closed_count(self):
        """position_monitor 的平倉數必須攤開在 log 裡 —— 它曾經被寫死為 0。"""
        result = {"checked": 3, "closed_count": 2, "skipped": [], "unprotected": []}
        runner = self._runner([job("position_monitor", 10, lambda: result)])
        runner.tick(now=0)

        logged = " ".join(str(c) for c in self.logger.info.call_args_list)
        self.assertIn("closed", logged)

    def test_write_status_survives_unwritable_path(self):
        runner = self._runner([job("a", 10)], tmpfile="/nonexistent-dir/status.json")
        runner.write_status()
        self.assertTrue(self.logger.exception.called)


class TestJobSetsPreserveServiceScope(unittest.TestCase):

    def test_all_sets_exist(self):
        self.assertEqual(
            sorted(JOB_SETS),
            sorted([JOB_SET_ALL, JOB_SET_POSITION, JOB_SET_OPPORTUNITY, JOB_SET_TRADER]),
        )

    def test_position_service_never_opens_positions(self):
        """agmcis-position 原本只管出場。統一排程器後不該突然開始開倉。"""
        names = [f.__name__ for f in JOB_SETS[JOB_SET_POSITION]]
        self.assertNotIn("_job_auto_trader", names)
        self.assertNotIn("_job_opportunity_scanner", names)
        self.assertIn("_job_position_monitor", names)

    def test_opportunity_service_only_scans(self):
        names = [f.__name__ for f in JOB_SETS[JOB_SET_OPPORTUNITY]]
        self.assertEqual(names, ["_job_opportunity_scanner"])

    def test_trader_service_scope(self):
        names = [f.__name__ for f in JOB_SETS[JOB_SET_TRADER]]
        self.assertNotIn("_job_position_monitor", names)
        self.assertIn("_job_auto_trader", names)

    def test_all_set_is_a_superset_of_every_other_set(self):
        full = set(f.__name__ for f in JOB_SETS[JOB_SET_ALL])
        for name, factories in JOB_SETS.items():
            self.assertTrue(
                set(f.__name__ for f in factories).issubset(full),
                f"{name} 有不在完整組合裡的任務",
            )

    def test_unknown_job_set_raises(self):
        from agmcis.scheduling.runner import build_jobs
        with self.assertRaises(ValueError):
            build_jobs("nonsense")
