"""
非同步任務(Master Prompt 第八十五節的 /api/backtest 與 /api/paper)。

那兩條一直沒做,理由是回測是長時間動作 —— 做成同步 HTTP 端點會變成
一個會逾時的請求,而且會佔住一個 uvicorn worker。

這組測試最在意三件事:
  一、同時只跑一個(併發回測會讓停損檢查延遲)。
  二、失敗的任務保留錯誤訊息(否則使用者只會重送)。
  三、動作端點是 POST,而且不在唯讀模組裡。
"""
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.lab import jobs as jobs_module

TMP = tempfile.mkdtemp(prefix="agmcis-test-jobs-")
_COUNTER = [0]


def store():
    _COUNTER[0] += 1
    return jobs_module.JobStore(
        path=os.path.join(TMP, f"jobs-{_COUNTER[0]}.json"),
    )


def runner(handlers=None, **kwargs):
    return jobs_module.JobRunner(
        store=store(), handlers=handlers or {"noop": lambda: {"ok": True}},
        **kwargs
    )


class TestSubmitting(unittest.TestCase):

    def test_a_job_gets_an_id_and_a_status(self):
        job = runner().submit("noop", run_in_background=False)

        self.assertTrue(job.job_id)
        self.assertEqual(job.status, jobs_module.DONE)

    def test_an_unregistered_kind_is_refused(self):
        """
        收下一個永遠不會被執行的任務,比拒絕它更糟:
        使用者會一直等一個不會來的結果。
        """
        with self.assertRaises(ValueError):
            runner().submit("魔法")

    def test_the_result_is_kept(self):
        current = runner(handlers={"x": lambda: {"answer": 42}})
        job = current.submit("x", run_in_background=False)

        self.assertEqual(job.result["answer"], 42)

    def test_a_non_dict_result_is_wrapped_not_dropped(self):
        current = runner(handlers={"x": lambda: "完成"})
        job = current.submit("x", run_in_background=False)

        self.assertEqual(job.result["value"], "完成")

    def test_params_are_passed_to_the_handler(self):
        seen = {}
        current = runner(handlers={
            "x": lambda **kwargs: seen.update(kwargs) or {"ok": True},
        })
        current.submit("x", {"symbol": "BTC/USDT"}, run_in_background=False)

        self.assertEqual(seen["symbol"], "BTC/USDT")


class TestFailuresAreKept(unittest.TestCase):
    """
    一個「送出去之後就再也查不到」的任務,使用者只會重送一次,
    然後兩個都失敗、兩份錯誤都沒人看到。
    """

    def _failing(self):
        def explode():
            raise RuntimeError("回測炸了")

        return runner(handlers={"x": explode})

    def test_a_failed_job_is_marked_failed(self):
        with patch.object(jobs_module, "logger"):
            job = self._failing().submit("x", run_in_background=False)

        self.assertEqual(job.status, jobs_module.FAILED)

    def test_the_error_message_survives(self):
        with patch.object(jobs_module, "logger"):
            job = self._failing().submit("x", run_in_background=False)

        self.assertIn("回測炸了", job.error)

    def test_the_traceback_is_kept_but_bounded(self):
        with patch.object(jobs_module, "logger"):
            job = self._failing().submit("x", run_in_background=False)

        self.assertIn("RuntimeError", job.error)
        self.assertLess(len(job.error), 4000)

    def test_a_failure_does_not_stop_the_next_job(self):
        current = jobs_module.JobRunner(store=store(), handlers={
            "bad": lambda: (_ for _ in ()).throw(RuntimeError("x")),
            "good": lambda: {"ok": True},
        })

        with patch.object(jobs_module, "logger"):
            current.submit("bad", run_in_background=False)
            good = current.submit("good", run_in_background=False)

        self.assertEqual(good.status, jobs_module.DONE)


class TestOnlyOneAtATime(unittest.TestCase):
    """
    回測吃 CPU,而這台機器同時還要跑排程、監控持倉、算指標。
    併發跑五個回測會讓停損檢查延遲 —— 而那是拿真錢在換一份報告。
    """

    def test_a_second_submission_queues_instead_of_running(self):
        started = []
        release = []

        def slow():
            started.append(time.time())
            while not release:
                time.sleep(0.005)
            return {"ok": True}

        current = runner(handlers={"slow": slow})
        first = current.submit("slow")

        deadline = time.time() + 2.0
        while time.time() < deadline and not started:
            time.sleep(0.005)

        second = current.submit("slow")
        self.assertEqual(second.status, jobs_module.QUEUED)
        self.assertEqual(len(started), 1, "第二個不該同時開始")

        release.append(True)

        deadline = time.time() + 3.0
        while time.time() < deadline:
            if current.store.get(second.job_id).status == jobs_module.DONE:
                break
            time.sleep(0.01)

        self.assertEqual(
            current.store.get(second.job_id).status, jobs_module.DONE,
            "排隊的任務最後還是要被跑掉",
        )
        self.assertEqual(
            current.store.get(first.job_id).status, jobs_module.DONE,
        )


class TestTheStore(unittest.TestCase):

    def test_a_job_round_trips(self):
        current = store()
        job = jobs_module.Job(kind="backtest", params={"candles": 3000})
        current.upsert(job)

        loaded = current.get(job.job_id)
        self.assertEqual(loaded.params["candles"], 3000)

    def test_old_jobs_are_trimmed(self):
        """舊的回測報告沒有人會回頭看,但檔案會一直長。"""
        current = jobs_module.JobStore(
            path=os.path.join(TMP, "trim.json"), max_kept=3,
        )
        for i in range(6):
            job = jobs_module.Job(kind="x")
            job.created_at = f"2026-01-0{i + 1}T00:00:00"
            current.upsert(job)

        self.assertEqual(len(current.load()), 3)

    def test_a_corrupt_file_gives_no_jobs_not_an_exception(self):
        current = store()
        current.path.parent.mkdir(parents=True, exist_ok=True)
        current.path.write_text("{ not json", encoding="utf-8")

        with patch.object(jobs_module, "logger"):
            self.assertEqual(current.load(), [])

    def test_recent_filters_by_kind(self):
        current = store()
        current.upsert(jobs_module.Job(kind="backtest"))
        current.upsert(jobs_module.Job(kind="paper"))

        self.assertEqual(len(current.recent(kind="paper")), 1)


class TestTheEndpointsAreActionsNotViews(unittest.TestCase):

    def test_backtest_and_paper_are_post(self):
        """
        GET 依定義不該變更狀態。任何爬蟲、預抓連結的聊天軟體、
        或瀏覽器的推測性載入,都可能在使用者不知情的情況下觸發它們。
        """
        from api import jobs as jobs_api

        methods = {
            route.path: set(route.methods) for route in jobs_api.router.routes
        }

        self.assertEqual(methods["/api/backtest"], {"POST"})
        self.assertEqual(methods["/api/paper"], {"POST"})

    def test_reading_a_job_is_get(self):
        from api import jobs as jobs_api

        methods = {
            route.path: set(route.methods) for route in jobs_api.router.routes
        }
        self.assertEqual(methods["/api/jobs"], {"GET"})

    def test_they_are_not_in_the_read_only_module(self):
        """
        把動作混進觀察模組裡,那條「所有路由都是 GET」的測試就得放寬,
        而放寬之後它就不再擋得住真正該擋的東西。
        """
        from api import transparency

        paths = {route.path for route in transparency.router.routes}
        self.assertNotIn("/api/backtest", paths)
        self.assertNotIn("/api/paper", paths)

    def test_a_missing_job_says_so(self):
        from api.jobs import api_job

        result = api_job("does-not-exist")
        self.assertFalse(result["found"])

    def test_they_require_a_key_like_everything_else(self):
        import inspect

        import main

        source = inspect.getsource(main)
        self.assertIn("jobs_router", source)
        # jobs_router 出現在那個掛 PROTECTED 的迴圈裡
        loop = source[source.index("for _router in ("):]
        self.assertIn("jobs_router", loop[:loop.index(")")])


class TestTheBacktestJobUsesTheSamePathAsTheScript(unittest.TestCase):
    """
    兩條會產生不同結果的回測入口,遲早會有人引用其中一條的數字
    去解釋另一條的行為。
    """

    def test_it_calls_the_strategy_optimizer(self):
        import inspect

        source = inspect.getsource(jobs_module.run_backtest_job)
        self.assertIn("get_strategy_optimizer", source)

    def test_it_separates_live_pipeline_passes_from_legacy_ones(self):
        """
        沒有 is_live_pipeline 標記的 PASS 是舊的 strategies/*.py 通過的,
        而它們不是 live 在用的東西。
        """
        rows = [
            {"strategy": "live_one", "verdict": "PASS", "is_live_pipeline": True},
            {"strategy": "old_one", "verdict": "PASS", "is_live_pipeline": False},
        ]

        with patch("strategy_optimizer.get_strategy_optimizer",
                   return_value={"all_results": rows, "strategy_summary": []}):
            result = jobs_module.run_backtest_job(symbols=["BTC/USDT"])

        self.assertEqual(result["live_pipeline_passed"], ["live_one"])
        self.assertEqual(result["legacy_passed"], ["old_one"])


if __name__ == "__main__":
    unittest.main()
