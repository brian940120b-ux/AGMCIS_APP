"""
Production Safety(Phase 16)。

三件事:
  1. Kill Switch 真的能平倉(之前它只會記錄「未接上平倉能力」)
  2. 跨行程限流,以及共用狀態拿不到時的降級行為
  3. 上線前檢查

第 2 點的降級行為是這一階段最重要的決定,所以測試最多。
"""
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.exchange.rate_limiter import RateLimiter
from agmcis.exchange.shared_rate_limit import SharedRateLimiter
from agmcis.risk import kill_switch as ks


class FakeStore:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = {}
        self.cooldowns = {}
        self.recorded = 0

    def _check(self):
        if self.fail:
            raise ConnectionError("資料庫掛了")

    def record_call(self, bucket):
        self._check()
        self.calls[bucket] = self.calls.get(bucket, 0) + 1
        self.recorded += 1

    def count_calls(self, bucket, period_seconds):
        self._check()
        return self.calls.get(bucket, 0)

    def oldest_call_age(self, bucket, period_seconds):
        self._check()
        return 0.0 if self.calls.get(bucket) else None

    def set_cooldown(self, bucket, seconds, reason=""):
        self._check()
        self.cooldowns[bucket] = seconds

    def cooldown_remaining(self, bucket):
        self._check()
        return self.cooldowns.get(bucket, 0.0)


def limiter(store=None, max_calls=5, period=10.0):
    clock = [0.0]
    local = RateLimiter(max_calls=max_calls, period_seconds=period,
                        name="test", clock=lambda: clock[0])
    shared = SharedRateLimiter(local, store=store, clock=lambda: clock[0])
    return shared, clock


class TestKillSwitchCanActuallyClose(unittest.TestCase):
    """
    一個按下去不會平倉的緊急按鈕,比沒有按鈕更危險:
    你會以為自己按過了。
    """

    def test_the_default_switch_reports_it_can_close_positions(self):
        switch = ks.build_wired_kill_switch()

        self.assertTrue(switch.status()["can_close_positions"])

    def test_it_closes_through_the_execution_engine(self):
        """不要在系統裡長出第二條平倉路徑。"""
        from agmcis.execution.engine import ExecutionResult

        engine = MagicMock()
        engine.close.return_value = ExecutionResult(ok=True, status="CLOSED")

        with patch("agmcis.execution.engine.get_engine", return_value=engine):
            switch = ks.build_wired_kill_switch()
            closer = switch._close_position
            closer({"symbol": "BTC/USDT"})

        engine.close.assert_called_once()
        self.assertEqual(engine.close.call_args.args[0], "BTC/USDT")

    def test_a_failed_close_raises_so_panic_records_it(self):
        """
        平倉失敗必須讓 panic() 記下來。安靜地當作成功,
        報告會說「已平倉」而部位還在。
        """
        from agmcis.execution.engine import ExecutionResult

        engine = MagicMock()
        engine.close.return_value = ExecutionResult(
            ok=False, status="CLOSE_FAILED", reason="沒有持倉",
        )

        with patch("agmcis.execution.engine.get_engine", return_value=engine):
            closer = ks.build_wired_kill_switch()._close_position

            with self.assertRaises(RuntimeError):
                closer({"symbol": "BTC/USDT"})

    def test_it_does_not_pretend_to_cancel_orders(self):
        """
        模擬盤沒有掛在市場上的單。假裝有這個能力比沒有更糟。
        """
        switch = ks.build_wired_kill_switch()

        self.assertFalse(switch.status()["can_cancel_orders"])


class TestSharedRateLimitHoldsTheLine(unittest.TestCase):

    def test_shared_usage_counts_against_the_budget(self):
        """
        這就是整個功能的重點:另一個 process 用掉的額度也要算。
        """
        store = FakeStore()
        store.calls["test"] = 5          # 別的 process 已經用滿

        shared, _ = limiter(store, max_calls=5)

        self.assertGreater(shared.wait_time(), 0)

    def test_a_call_is_recorded_for_other_processes_to_see(self):
        store = FakeStore()
        shared, _ = limiter(store)

        shared.acquire(sleep=lambda _: None)

        self.assertEqual(store.recorded, 1)

    def test_a_shared_cooldown_blocks_every_process(self):
        """
        一個 process 被限流了,其他 process 繼續打只會讓封鎖時間變長。
        """
        store = FakeStore()
        store.cooldowns["test"] = 30.0

        shared, _ = limiter(store)

        self.assertGreaterEqual(shared.wait_time(), 30.0)

    def test_penalise_writes_the_cooldown_to_shared_state(self):
        store = FakeStore()
        shared, _ = limiter(store)

        shared.penalise(45.0, reason="429")

        self.assertEqual(store.cooldowns["test"], 45.0)

    def test_the_stricter_of_the_two_layers_wins(self):
        store = FakeStore()
        shared, clock = limiter(store, max_calls=2)

        # 行程內用掉兩次,共用狀態是空的
        shared.local.acquire(sleep=lambda _: None)
        shared.local.acquire(sleep=lambda _: None)

        self.assertGreater(shared.wait_time(), 0)


class TestDegradationIsVisible(unittest.TestCase):
    """
    共用狀態拿不到時**退回行程內限流**,不是擋住所有請求。

    理由:限流變鬆的後果是被交易所暫時封鎖,那是可回復的;
    整個系統停擺會讓已有部位失去監控,那更危險。
    但這個降級必須看得見。
    """

    def test_a_broken_store_does_not_block_everything(self):
        shared, _ = limiter(FakeStore(fail=True))

        self.assertEqual(shared.wait_time(), 0.0)

    def test_degradation_is_counted_and_explained(self):
        shared, _ = limiter(FakeStore(fail=True))

        with patch("agmcis.exchange.shared_rate_limit.logger"):
            shared.wait_time()

        self.assertGreater(shared.degraded_count, 0)
        self.assertIn("ConnectionError", shared.last_degrade_reason)

    def test_repeated_failures_stop_hammering_the_database(self):
        """每次失敗都是一次資料庫往返。連續失敗就先退避。"""
        from agmcis.exchange import shared_rate_limit

        shared, _ = limiter(FakeStore(fail=True))

        with patch.object(shared_rate_limit, "logger"):
            for _ in range(shared_rate_limit.MAX_CONSECUTIVE_FAILURES):
                shared.wait_time()

        self.assertFalse(shared.shared_available)

    def test_it_logs_at_error_level_when_it_gives_up(self):
        from agmcis.exchange import shared_rate_limit

        shared, _ = limiter(FakeStore(fail=True))

        with patch.object(shared_rate_limit, "logger") as logger:
            for _ in range(shared_rate_limit.MAX_CONSECUTIVE_FAILURES):
                shared.wait_time()

        logger.error.assert_called()

    def test_it_recovers_when_the_store_comes_back(self):
        store = FakeStore(fail=True)
        shared, _ = limiter(store)

        with patch("agmcis.exchange.shared_rate_limit.logger"):
            shared.wait_time()
            store.fail = False
            shared.wait_time()

        self.assertTrue(shared.shared_available)

    def test_status_says_whether_the_shared_layer_is_working(self):
        """降級不能只出現在 log 裡 —— 狀態端點要看得到。"""
        shared, _ = limiter(FakeStore())
        status = shared.status()

        self.assertTrue(status["shared"])
        self.assertIn("shared_used", status)

    def test_a_local_only_limiter_still_reports_its_own_status(self):
        shared, _ = limiter(FakeStore(fail=True))

        with patch("agmcis.exchange.shared_rate_limit.logger"):
            status = shared.status()

        self.assertIn("max_calls", status)


class TestAdapterWiring(unittest.TestCase):

    def test_shared_rate_limiting_is_off_by_default(self):
        """
        它需要 migration 006 的資料表。表不存在時每次呼叫都會先失敗
        再降級,那比不開更慢。
        """
        from agmcis.config import settings

        self.assertFalse(settings.EXCHANGE_SHARED_RATE_LIMIT)

    def test_the_adapter_builds_a_plain_limiter_when_it_is_off(self):
        from agmcis.exchange.bingx import adapter

        self.assertIsInstance(adapter._build_rate_limiter(), RateLimiter)

    def test_the_adapter_wraps_it_when_it_is_on(self):
        from agmcis.config import settings
        from agmcis.exchange.bingx import adapter

        with patch.object(settings, "EXCHANGE_SHARED_RATE_LIMIT", True):
            built = adapter._build_rate_limiter()

        self.assertIsInstance(built, SharedRateLimiter)


class TestPreflight(unittest.TestCase):

    def _script(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "scripts", "preflight.py",
        )
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_it_is_read_only(self):
        """上線前檢查不該改任何東西。"""
        import ast

        tree = ast.parse(self._script())
        forbidden = {
            "create_order", "create_paper_trade", "close_paper_trade",
            "arm", "panic", "update_account", "insert_trade",
        }

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = (
                func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name) else None
            )
            with self.subTest(call=called):
                self.assertNotIn(called, forbidden)

    def test_it_never_prints_a_secret(self):
        """檢查腳本會被貼到聊天視窗裡。它不可以印出金鑰的值。"""
        source = self._script()

        for banned in ("EXCHANGE_API_SECRET", "API_SECRET",
                       "os.getenv(\"DB_PASSWORD\")[", "settings.EXCHANGE_API_KEY"):
            with self.subTest(name=banned):
                self.assertNotIn(banned, source)

    def test_a_live_trading_mode_is_a_blocker(self):
        """系統裡沒有 LiveBroker,設成 live 沒有意義而且會誤導人。"""
        source = self._script()

        self.assertIn("TRADING_MODE=live", source)
        self.assertIn("BLOCKER", source)


if __name__ == "__main__":
    unittest.main()
