"""
EMERGENCY PROTECTION(Master Prompt 第十八節的六步驟)。

這個模組唯一的工作是回答一個問題:**這個部位現在到底有沒有停損?**
它最容易出錯的地方不是流程順序,而是「不確定」被當成「沒問題」——
所以這裡大部分的測試都在測失敗路徑,不是成功路徑。
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.execution import emergency
from agmcis.execution.broker import Broker, FillResult


class StubBroker(Broker):
    """
    可以逐項指定「哪一步會怎麼壞掉」的 broker。

    預設是最糟的情況:沒有保護、重試沒有用、不能縮倉、但平得掉。
    """

    name = "stub"

    def __init__(self, protected=False, protected_after_retries=None,
                 verify_error=None, stop_error=None, stop_result=True,
                 reduce_result=None, protected_after_reduce=False,
                 close_ok=True, close_error=None):
        self.protected = protected
        self.protected_after_retries = protected_after_retries
        self.verify_error = verify_error
        self.stop_error = stop_error
        self.stop_result = stop_result
        self.reduce_result = reduce_result
        self.protected_after_reduce = protected_after_reduce
        self.close_ok = close_ok
        self.close_error = close_error

        self.verify_calls = 0
        self.stop_attempts = []
        self.reduced = []
        self.closed = []

    def has_protection(self, symbol):
        self.verify_calls += 1
        if self.verify_error:
            raise self.verify_error
        return self.protected

    def ensure_stop_loss(self, symbol, stop_price):
        if self.stop_error:
            raise self.stop_error
        self.stop_attempts.append(stop_price)
        if (self.protected_after_retries is not None
                and len(self.stop_attempts) >= self.protected_after_retries):
            self.protected = True
        return self.stop_result

    def reduce_position(self, symbol, fraction, reason=""):
        if self.reduce_result is None:
            raise NotImplementedError
        self.reduced.append(fraction)
        if self.reduce_result and self.protected_after_reduce:
            self.protected = True
        return self.reduce_result

    def close_position(self, symbol, price=None, reason=""):
        if self.close_error:
            raise self.close_error
        self.closed.append(reason)
        return FillResult(
            ok=self.close_ok,
            reason=None if self.close_ok else "交易所拒絕",
        )


def run(broker, **kwargs):
    kwargs.setdefault("pause_file", os.path.join(PAUSE_DIR, "pause.flag"))
    kwargs.setdefault("notifier", lambda message: True)
    with patch.object(emergency, "logger"):
        return emergency.protect(broker, "BTC/USDT", 97.0, **kwargs)


PAUSE_DIR = tempfile.mkdtemp(prefix="agmcis-test-emergency-")


class TestTheHappyPathDoesNothing(unittest.TestCase):

    def test_a_protected_position_never_enters_the_emergency_flow(self):
        broker = StubBroker(protected=True)
        outcome = run(broker)

        self.assertEqual(outcome.status, emergency.PROTECTED)
        self.assertTrue(outcome.protected)
        self.assertEqual(broker.stop_attempts, [])
        self.assertEqual(broker.closed, [])
        self.assertFalse(outcome.new_orders_disabled)


class TestRetry(unittest.TestCase):

    def test_the_stop_is_retried_the_configured_number_of_times(self):
        broker = StubBroker()
        run(broker, retries=3)

        self.assertEqual(len(broker.stop_attempts), 3)

    def test_it_stops_retrying_as_soon_as_the_stop_exists(self):
        broker = StubBroker(protected_after_retries=2)
        outcome = run(broker, retries=5)

        self.assertEqual(outcome.status, emergency.PROTECTED_ON_RETRY)
        self.assertEqual(len(broker.stop_attempts), 2)

    def test_the_stop_price_is_passed_through_unchanged(self):
        """重試掛的必須是原本那個停損價,不是重算出來的。"""
        broker = StubBroker()
        run(broker, retries=1)

        self.assertEqual(broker.stop_attempts, [97.0])

    def test_retries_are_zero_when_configured_that_way(self):
        broker = StubBroker()
        run(broker, retries=0)

        self.assertEqual(broker.stop_attempts, [])


class TestUncertaintyIsTreatedAsUnprotected(unittest.TestCase):
    """
    這一組是這個模組存在的理由。
    「查不到」「不支援」「拋例外」全部必須走到「沒有保護」那一邊。
    """

    def test_a_verify_that_raises_counts_as_unprotected(self):
        broker = StubBroker(protected=True, verify_error=RuntimeError("查不到"))
        outcome = run(broker)

        self.assertFalse(outcome.protected)
        self.assertEqual(outcome.status, emergency.CLOSED)

    def test_a_broker_without_has_protection_counts_as_unprotected(self):
        class NoVerify(StubBroker):
            def has_protection(self, symbol):
                raise NotImplementedError

        outcome = run(NoVerify())
        self.assertEqual(outcome.status, emergency.CLOSED)

    def test_a_retry_that_raises_does_not_stop_the_flow(self):
        broker = StubBroker(stop_error=RuntimeError("429"))
        outcome = run(broker, retries=2)

        self.assertEqual(outcome.status, emergency.CLOSED)
        self.assertIn("retry1:error:RuntimeError", outcome.steps)

    def test_a_successful_retry_call_is_not_trusted_without_verification(self):
        """
        送單成功、交易所回 200、停損單卻因為觸發價越過現價而被撤掉 ——
        這在實盤是真的會發生的。ensure_stop_loss() 回 True 不算數。
        """
        broker = StubBroker(stop_result=True)   # 永遠回 True,但 protected 永遠 False
        outcome = run(broker, retries=2)

        self.assertEqual(outcome.status, emergency.CLOSED)
        self.assertGreaterEqual(broker.verify_calls, 3)

    def test_a_broker_that_cannot_reduce_goes_straight_to_close(self):
        broker = StubBroker()   # reduce_position 拋 NotImplementedError
        outcome = run(broker)

        self.assertEqual(outcome.status, emergency.CLOSED)
        self.assertIn("reduce:unsupported", outcome.steps)

    def test_a_reduce_that_raises_goes_to_close(self):
        class BadReduce(StubBroker):
            def reduce_position(self, symbol, fraction, reason=""):
                raise RuntimeError("縮不了")

        outcome = run(BadReduce())
        self.assertEqual(outcome.status, emergency.CLOSED)


class TestReduce(unittest.TestCase):

    def test_a_reduced_position_that_gets_its_stop_is_kept(self):
        broker = StubBroker(reduce_result=True, protected_after_reduce=True)
        outcome = run(broker)

        self.assertEqual(outcome.status, emergency.PROTECTED_AFTER_REDUCE)
        self.assertTrue(outcome.protected)
        self.assertEqual(broker.closed, [])
        self.assertEqual(outcome.reduced_fraction, 0.5)

    def test_a_reduced_position_that_still_has_no_stop_is_closed(self):
        broker = StubBroker(reduce_result=True, protected_after_reduce=False)
        outcome = run(broker)

        self.assertEqual(outcome.status, emergency.CLOSED)
        self.assertEqual(broker.reduced, [0.5])

    def test_the_reduce_fraction_is_configurable(self):
        broker = StubBroker(reduce_result=True, protected_after_reduce=True)
        outcome = run(broker, reduce_fraction=0.75)

        self.assertEqual(broker.reduced, [0.75])
        self.assertEqual(outcome.reduced_fraction, 0.75)


class TestClose(unittest.TestCase):

    def test_a_position_that_cannot_be_closed_is_stuck(self):
        broker = StubBroker(close_ok=False)
        outcome = run(broker)

        self.assertEqual(outcome.status, emergency.STUCK)
        self.assertTrue(outcome.needs_human)
        self.assertFalse(outcome.protected)

    def test_a_close_that_raises_is_stuck_not_closed(self):
        broker = StubBroker(close_error=RuntimeError("斷線"))
        outcome = run(broker)

        self.assertEqual(outcome.status, emergency.STUCK)
        self.assertIn("RuntimeError", outcome.reason)

    def test_an_injected_close_is_used_instead_of_the_broker(self):
        """
        Execution Engine 的平倉不只是送單,還要推狀態機、寫 order store。
        這個模組不該知道那些事,所以平倉是注入的。
        """
        broker = StubBroker()
        calls = []

        outcome = run(
            broker,
            close_position=lambda symbol, reason: calls.append(symbol) or True,
        )

        self.assertEqual(calls, ["BTC/USDT"])
        self.assertEqual(broker.closed, [], "注入平倉之後不該再直接呼叫 broker")
        self.assertEqual(outcome.status, emergency.CLOSED)


class TestDisableNewOrders(unittest.TestCase):

    def setUp(self):
        self.flag = os.path.join(PAUSE_DIR, f"flag-{id(self)}.flag")

    def tearDown(self):
        if os.path.exists(self.flag):
            os.remove(self.flag)

    def test_the_flag_is_written_when_retries_fail(self):
        run(StubBroker(), pause_file=self.flag)

        self.assertTrue(os.path.exists(self.flag))
        self.assertIn("EMERGENCY_PROTECTION",
                      open(self.flag, encoding="utf-8").read())

    def test_the_flag_is_not_written_when_the_retry_works(self):
        run(StubBroker(protected_after_retries=1), pause_file=self.flag)

        self.assertFalse(os.path.exists(self.flag))

    def test_the_flag_is_written_even_when_the_position_is_saved_by_reducing(self):
        outcome = run(
            StubBroker(reduce_result=True, protected_after_reduce=True),
            pause_file=self.flag,
        )

        self.assertTrue(outcome.protected)
        self.assertTrue(os.path.exists(self.flag))

    def test_a_flag_that_cannot_be_written_does_not_swallow_the_failure(self):
        """
        寫不進去代表「這個通道掛不上停損」沒有被記住,下一個訊號會照常開倉。
        流程必須繼續(倉位還是要處理),但 new_orders_disabled 必須誠實回 False。
        """
        outcome = run(
            StubBroker(),
            pause_file=os.path.join(PAUSE_DIR, "no-such-dir", "x.flag"),
        )

        self.assertFalse(outcome.new_orders_disabled)
        self.assertEqual(outcome.status, emergency.CLOSED)

    def test_the_flag_actually_blocks_the_risk_engine(self):
        """
        不要有第二個「交易是否暫停」的真相來源。

        這個測試不是檢查兩邊寫了同一個常數名字,是真的跑一次:
        緊急保護寫下旗標之後,Risk Engine 的帳戶層閘門必須擋住新單。
        """
        from agmcis.risk.engine import AccountState, RiskEngine

        limits = {
            "TRADING_PAUSE_FILE": self.flag,
            "EMERGENCY_STOP_FILE": os.path.join(PAUSE_DIR, "no-such-stop"),
        }
        healthy = AccountState(equity=10000.0, available_balance=10000.0,
                               profit_factor=1.5)
        engine = RiskEngine(limits=limits)

        self.assertTrue(
            engine.check_gate(healthy).allowed,
            "前提不成立:旗標還沒寫的時候閘門本來就該是開的",
        )

        run(StubBroker(), pause_file=self.flag)

        gate = engine.check_gate(healthy)
        self.assertFalse(gate.allowed)
        self.assertIn("TRADING_PAUSE_FLAG", gate.blockers)


class TestNotify(unittest.TestCase):

    def test_the_user_is_notified_of_every_emergency(self):
        messages = []
        run(StubBroker(), notifier=messages.append)

        self.assertEqual(len(messages), 1)
        self.assertIn("EMERGENCY", messages[0])
        self.assertIn("BTC/USDT", messages[0])

    def test_a_stuck_position_says_it_needs_a_human(self):
        messages = []
        run(StubBroker(close_ok=False), notifier=messages.append)

        self.assertIn("人工介入", messages[0])

    def test_a_notification_that_fails_does_not_change_the_outcome(self):
        """倉位已經平掉了。通知送不出去不會讓它變回沒平掉。"""
        def broken(message):
            raise RuntimeError("Telegram 掛了")

        outcome = run(StubBroker(), notifier=broken)
        self.assertEqual(outcome.status, emergency.CLOSED)

    def test_nothing_is_sent_when_the_position_was_already_protected(self):
        messages = []
        run(StubBroker(protected=True), notifier=messages.append)

        self.assertEqual(messages, [])


class TestTheStepTrailIsReadable(unittest.TestCase):
    """
    事後要回答「當時到底發生了什麼」,靠的是這串步驟紀錄。
    它進 ExecutionResult、進 log、也進 Telegram 訊息。
    """

    def test_every_step_is_recorded_in_order(self):
        broker = StubBroker(reduce_result=True)
        outcome = run(broker, retries=1)

        self.assertEqual(
            outcome.steps,
            [
                "verify:missing",
                "retry1:sent", "verify:missing",
                "reduce:ok",
                "retry_after_reduce:sent", "verify:missing",
                "close:ok",
            ],
        )


if __name__ == "__main__":
    unittest.main()
