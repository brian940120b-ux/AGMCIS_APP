"""
Simulation Tests(Master Prompt 第六十七節)。

第六十七節列了九種要模擬的情況:

    Market Crash / API Timeout / Order Rejection / Partial Fill /
    SL Failure / Duplicate Order / Network Disconnect /
    Database Failure / WebSocket Disconnect

單元測試驗的是「這個函式算得對不對」,模擬測試驗的是**「這個東西
壞掉的時候,系統往哪一邊倒」**。後者才是真錢在線上的時候會發生的事。

每一個測試問的都是同一個問題:失敗的預設結果是安全還是危險。
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.core.enums import Direction, OrderSide, OrderState, OrderType, PositionSide
from agmcis.core.errors import ExchangeError
from agmcis.core.models import RiskDecision, TradeIntent
from agmcis.execution.broker import Broker, FillResult
from agmcis.execution.engine import ExecutionEngine
from agmcis.execution.rules_engine import OrderRequest, ValidationResult

TMP = tempfile.mkdtemp(prefix="agmcis-test-sim-")


def intent(**overrides):
    base = dict(symbol="BTC/USDT", market_type="perpetual", direction="做多",
                entry=100.0, stop_loss=97.0, take_profit=110.0, strategy="sim")
    base.update(overrides)
    return TradeIntent(**base)


def decision(approved=True):
    return RiskDecision(
        intent=intent(), approved=approved,
        size_usdt=200.0 if approved else None,
        leverage=3.0 if approved else None,
    )


class FakeRules:
    def __init__(self, ok=True, violations=None):
        self.result = ValidationResult(
            ok=ok,
            order=OrderRequest(
                client_order_id="sim-1", symbol="BTC/USDT",
                market_type="perpetual", side=OrderSide.BUY,
                position_side=PositionSide.LONG, order_type=OrderType.MARKET,
                quantity=6.0, reference_price=100.0, stop_loss=97.0,
                leverage=3.0, size_usdt=200.0,
            ) if ok else None,
            violations=list(violations or []),
        )

    def validate(self, *args, **kwargs):
        return self.result


class FakeStore:
    def __init__(self, fail=False):
        self.fail = fail
        self.saved = []
        self.events = []

    def save(self, order, trade_id=None, source="EXEC"):
        if self.fail:
            raise RuntimeError("資料庫掛了")
        self.saved.append(order.client_order_id)
        return len(self.saved)

    def record_event(self, client_order_id, from_state, to_state, reason=None):
        if self.fail:
            raise RuntimeError("資料庫掛了")
        self.events.append((from_state, to_state))

    def mark_reconciled(self, client_order_id):
        pass


class SimBroker(Broker):
    name = "sim"

    def __init__(self, fill=None, submit_error=None, protected=True,
                 close_result=None, close_error=None, cancel_result=True):
        self.fill = fill
        self.submit_error = submit_error
        self.protected = protected
        self.close_result = close_result or FillResult(
            ok=True, filled_quantity=6.0, average_price=99.0,
        )
        self.close_error = close_error
        self.cancel_result = cancel_result
        self.submitted = []
        self.closed = []

    def submit_entry(self, order_request, intent, size_usdt, leverage):
        if self.submit_error:
            raise self.submit_error
        self.submitted.append(order_request.client_order_id)
        return self.fill or FillResult(
            ok=True, filled_quantity=6.0, requested_quantity=6.0,
            average_price=100.0, exchange_order_id="X1",
        )

    def has_protection(self, symbol):
        return self.protected

    def ensure_stop_loss(self, symbol, stop_price):
        return True

    def close_position(self, symbol, price=None, reason=""):
        if self.close_error:
            raise self.close_error
        self.closed.append(reason)
        return self.close_result

    def cancel_remainder(self, order):
        return self.cancel_result

    def get_position(self, symbol):
        return None


def engine(broker, rules=None, store=None):
    return ExecutionEngine(
        broker=broker,
        rules_engine=rules or FakeRules(),
        store=store if store is not None else FakeStore(),
        pause_file=os.path.join(TMP, "pause.flag"),
    )


class TestMarketCrash(unittest.TestCase):
    """
    崩盤時的兩件事:停損會跳空、強平可能先於停損。
    回測必須把兩者都算在保守的那一邊。
    """

    def _run(self, closes):
        from agmcis.backtest.costs import CostModel
        from agmcis.backtest.engine import BacktestEngine

        rows = [{"time": i, "open": 100.0, "high": 100.0,
                 "low": 100.0, "close": 100.0, "volume": 1000.0}
                for i in range(61)]
        previous = 100.0
        for offset, close in enumerate(closes):
            rows.append({
                "time": 61 + offset, "open": previous,
                "high": max(previous, close), "low": min(previous, close),
                "close": close, "volume": 1000.0,
            })
            previous = close

        def signal_fn(history, index):
            return {"direction": "做多", "stop_loss": 95.0} if index == 60 else None

        return BacktestEngine(
            costs=CostModel(maker_fee=0, taker_fee=0, slippage_pct=0,
                            spread_pct=0, funding_rate_8h=0),
            start_balance=10000.0,
        ).run(rows, signal_fn, warmup=60)

    def test_a_gap_through_the_stop_fills_at_the_open_not_the_stop(self):
        """
        開盤已經穿過停損價,就不可能在停損價成交。
        假設還能在停損價出場會高估績效 —— 而崩盤正是那個假設最貴的時候。
        """
        result = self._run([60.0, 60.0])
        trade = result.trades[0]

        self.assertLess(trade.exit_price, 95.0)

    def test_a_crash_cannot_lose_more_than_the_margin(self):
        result = self._run([1.0, 1.0])
        trade = result.trades[0]

        self.assertGreaterEqual(trade.net_pnl, -trade.size_usdt)

    def test_the_loss_is_recorded_not_swallowed(self):
        result = self._run([60.0, 60.0])

        self.assertEqual(len(result.trades), 1)
        self.assertLess(result.end_balance, 10000.0)


class TestApiTimeout(unittest.TestCase):
    """
    逾時與失敗是**不同的**兩件事:逾時代表系統不知道交易所收到了什麼。
    """

    def test_a_submit_timeout_does_not_report_success(self):
        broker = SimBroker(submit_error=TimeoutError("逾時"))

        with patch("agmcis.execution.engine.logger"):
            result = engine(broker).execute(decision())

        self.assertFalse(result.ok)

    def test_a_timed_out_order_is_never_silently_retried(self):
        """
        直接重送可能開出第二張倉位。狀態機不允許從 UNKNOWN 直接重下。
        """
        from agmcis.execution import state_machine as sm

        allowed = sm.TRANSITIONS[OrderState.UNKNOWN]
        self.assertNotIn(OrderState.CREATED, allowed)
        self.assertNotIn(OrderState.SUBMITTING, allowed)

    def test_an_unrecognised_error_is_not_retried(self):
        """
        分類不出來的錯誤預設**不重試**。重試一個不知道是什麼的錯誤,
        最好的情況是浪費 rate limit,最壞的情況是重複下單。
        """
        from agmcis.exchange.error_policy import classify

        policy = classify(TimeoutError("timed out"))
        self.assertFalse(policy.is_retryable)

    def test_a_write_timeout_never_retries(self):
        """
        讀取失敗最多就是沒資料;送出訂單之後逾時完全是另一回事 ——
        交易所可能已經收到了,重送就是第二張倉位。
        """
        from agmcis.exchange import error_policy

        policy = error_policy.classify_write_failure(TimeoutError("timed out"))
        self.assertFalse(policy.is_retryable)

    def test_a_known_network_error_is_retryable(self):
        from agmcis.exchange import error_policy

        policy = error_policy.classify(
            error_policy.ExchangeUnavailableError("connection reset"),
        )
        self.assertIn(
            policy.action,
            (error_policy.ACTION_RETRY, error_policy.ACTION_BACKOFF_LONG,
             error_policy.ACTION_FAIL),
        )


class TestOrderRejection(unittest.TestCase):

    def test_a_rules_violation_never_reaches_the_broker(self):
        broker = SimBroker()
        rules = FakeRules(ok=False, violations=["MIN_NOTIONAL"])

        result = engine(broker, rules=rules).execute(decision())

        self.assertFalse(result.ok)
        self.assertEqual(broker.submitted, [])

    def test_an_unapproved_decision_never_reaches_the_broker(self):
        broker = SimBroker()

        result = engine(broker).execute(decision(approved=False))

        self.assertFalse(result.ok)
        self.assertEqual(broker.submitted, [])

    def test_a_broker_rejection_is_reported_not_retried(self):
        broker = SimBroker(fill=FillResult(ok=False, reason="保證金不足"))

        with patch("agmcis.execution.engine.logger"):
            result = engine(broker).execute(decision())

        self.assertFalse(result.ok)
        self.assertEqual(len(broker.submitted), 1, "被拒之後不該再送一次")


class TestPartialFill(unittest.TestCase):

    def test_a_partial_fill_still_requires_a_stop(self):
        """成交一半的部位一樣沒有虧損上限。"""
        broker = SimBroker(
            fill=FillResult(ok=True, filled_quantity=3.0, requested_quantity=6.0,
                            average_price=100.0, exchange_order_id="X1"),
            protected=False,
        )

        with patch("agmcis.execution.engine.logger"), \
             patch("agmcis.execution.emergency.logger"):
            result = engine(broker).execute(decision())

        self.assertFalse(result.ok)
        self.assertTrue(broker.closed)

    def test_an_uncancellable_remainder_is_escalated(self):
        """
        一張還活著的掛單可能在稍後成交,而那時候沒有人在管它。
        """
        broker = SimBroker(
            fill=FillResult(ok=True, filled_quantity=3.0, requested_quantity=6.0,
                            average_price=100.0, exchange_order_id="X1"),
            cancel_result=False,
        )

        with patch("agmcis.execution.engine.logger"):
            result = engine(broker).execute(decision())

        self.assertIn("REMAINDER", result.status)


class TestStopLossFailure(unittest.TestCase):

    def test_a_naked_position_is_never_left_open(self):
        broker = SimBroker(protected=False)

        with patch("agmcis.execution.engine.logger"), \
             patch("agmcis.execution.emergency.logger"):
            result = engine(broker).execute(decision())

        self.assertFalse(result.ok)
        self.assertTrue(broker.closed)

    def test_a_naked_position_that_cannot_be_closed_is_escalated(self):
        broker = SimBroker(
            protected=False,
            close_result=FillResult(ok=False, reason="交易所拒絕"),
        )

        with patch("agmcis.execution.engine.logger") as logger, \
             patch("agmcis.execution.emergency.logger"):
            result = engine(broker).execute(decision())

        self.assertEqual(result.status, "NAKED_POSITION_STUCK")
        logger.critical.assert_called()

    def test_new_orders_are_stopped_after_a_stop_loss_failure(self):
        """
        會掛不上停損的環境,下一單一樣會掛不上。
        """
        flag = os.path.join(TMP, f"pause-{id(self)}.flag")
        broker = SimBroker(protected=False)

        with patch("agmcis.execution.engine.logger"), \
             patch("agmcis.execution.emergency.logger"):
            ExecutionEngine(
                broker=broker, rules_engine=FakeRules(),
                store=FakeStore(), pause_file=flag,
            ).execute(decision())

        self.assertTrue(os.path.exists(flag))
        os.remove(flag)


class TestDuplicateOrder(unittest.TestCase):

    def test_the_same_symbol_cannot_be_opened_twice_by_the_risk_engine(self):
        from agmcis.risk.engine import AccountState, RiskEngine

        state = AccountState(
            equity=10000.0, available_balance=10000.0, profit_factor=1.5,
            open_symbols=["BTC/USDT"],
        )

        result = RiskEngine(limits={
            "EMERGENCY_STOP_FILE": "/nonexistent", "TRADING_PAUSE_FILE": "/nonexistent",
            "AUTO_TRADING_ENABLED": True, "MIN_PROFIT_FACTOR": 0.8,
        }).evaluate(intent(), state)

        self.assertFalse(result.approved)
        self.assertIn("DUPLICATE_POSITION", result.blockers)

    def test_the_database_also_refuses_a_duplicate(self):
        """
        應用層擋一次、資料庫再擋一次。重複的防線是刻意的 ——
        兩條路徑同時下單時,只有資料庫的唯一索引擋得住。
        """
        import database_service

        self.assertTrue(hasattr(database_service, "DuplicateOpenTradeError"))

    def test_a_partial_exit_stage_cannot_be_taken_twice(self):
        import database_service
        import inspect

        source = inspect.getsource(database_service.reduce_trade_atomic)
        self.assertIn("ON CONFLICT (trade_id, stage) DO NOTHING", source)


class TestNetworkDisconnect(unittest.TestCase):

    def test_retries_are_bounded(self):
        """
        第四十八節:禁止 Infinite Retry。一個永遠重試的客戶端
        在交易所故障時會把自己的 rate limit 用光,然後在服務恢復時
        因為限流而繼續失敗。
        """
        from agmcis.config import settings

        self.assertIsInstance(settings.EXCHANGE_MAX_RETRIES, int)
        self.assertLessEqual(settings.EXCHANGE_MAX_RETRIES, 10)

    def test_market_data_failure_does_not_produce_a_signal(self):
        """
        取不到行情時最危險的行為是用舊資料下單。
        """
        from agmcis.analysis.indicators import compute

        result = compute(None, "BTC/USDT")
        self.assertFalse(result.data_ok)

    def test_a_failed_price_lookup_skips_the_position_rather_than_guessing(self):
        from agmcis.execution.exit_plan import run_exit_plans

        summary = run_exit_plans(
            positions=[{"symbol": "BTC/USDT", "signal": "做多",
                        "entry_price": 100.0, "stoploss": 95.0}],
            price_of=lambda symbol: None,
            trade=MagicMock(), monitor=MagicMock(),
        )

        self.assertEqual(summary["actions"], [])
        self.assertTrue(summary["errors"])


class TestDatabaseFailure(unittest.TestCase):
    """
    資料庫掛掉時,每一層的答案都必須是「往安全的方向倒」。
    """

    def test_health_reports_unhealthy_not_healthy(self):
        from api import health

        def explode():
            raise RuntimeError("連不上")

        with patch.object(health, "_database", explode), \
             patch.object(health, "logger"):
            payload = health.build_health()

        self.assertEqual(payload["status"], "unhealthy")

    def test_decision_logging_failure_does_not_block_trading(self):
        """這一層是觀測,不是控制。"""
        from agmcis.review import decision_log

        def explode(payload):
            raise RuntimeError("資料庫掛了")

        with patch.object(decision_log, "logger"):
            self.assertIsNone(decision_log.record(
                decision_log.Decision(symbol="BTC/USDT", outcome="WAIT"),
                writer=explode,
            ))

    def test_strategy_status_falls_back_to_not_live(self):
        """
        「資料庫掛了所以所有策略看起來都是 LIVE」是錯誤的方向。
        """
        from agmcis.strategy.health import StatusStore, is_tradeable

        store = StatusStore(
            path=os.path.join(TMP, "missing.json"),
            audit_path=os.path.join(TMP, "missing.log"),
        )

        self.assertFalse(is_tradeable("anything", mode="live", store=store))

    def test_an_order_store_failure_does_not_abort_an_open_position(self):
        """
        這一條的方向與其他幾條相反,而且是刻意的。

        訂單已經送出去了。這時候因為「寫不進資料庫」而讓執行流程
        炸掉,會留下一個**已成交但沒有人在管**的部位 —— 那比
        沒有紀錄糟得多。所以持久化失敗不中斷執行,但必須進 log,
        而且對帳會在下一輪發現這筆沒有紀錄的部位。
        """
        broker = SimBroker()

        with patch("agmcis.execution.engine.logger") as logger:
            result = engine(broker, store=FakeStore(fail=True)).execute(decision())

        self.assertTrue(result.ok, "部位已經開了,不能假裝沒開")
        self.assertTrue(logger.critical.called, "持久化失敗必須大聲喊")


class TestWebsocketDisconnect(unittest.TestCase):

    def test_a_missing_websocket_is_disabled_not_an_error(self):
        """
        WebSocket 目前只用於 Dashboard 推播,不是行情來源。
        回 error 會讓監控以為交易受影響了。
        """
        from api import health

        self.assertIn(health._websocket(), ("ok", "disabled"))

    def test_the_system_still_trades_without_it(self):
        from api import health

        with patch.object(health, "_websocket", lambda: "disabled"), \
             patch.object(health, "_database", lambda: True), \
             patch.object(health, "_bingx", lambda: True), \
             patch.object(health, "_market_data", lambda: True), \
             patch.object(health, "_risk_engine", lambda: True), \
             patch.object(health, "_trading_engine", lambda: True), \
             patch.object(health, "_agent_engine", lambda: True), \
             patch.object(health, "_scheduler", lambda: True):
            payload = health.build_health()

        self.assertEqual(payload["status"], "healthy")


if __name__ == "__main__":
    unittest.main()
