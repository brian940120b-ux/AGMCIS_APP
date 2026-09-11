"""
Execution Engine(Phase 12)。

這一層唯一的判斷是「現在到哪一步了」。它不決定要不要交易、
不決定倉位大小、不決定交易所收不收。

最重要的測試是**裸倉**:開倉成功但停損沒掛上去的部位沒有虧損上限,
必須立刻平掉。寧可平掉一個可能會賺的倉位,也不要留一個沒有停損的倉位。
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.core.enums import MarketType, OrderSide, OrderState, OrderType, PositionSide
from agmcis.core.models import Order, RiskDecision, TradeIntent
from agmcis.execution import engine as engine_module
from agmcis.execution import state_machine as sm
from agmcis.execution.broker import Broker, FillResult
from agmcis.execution.engine import ExecutionEngine
from agmcis.execution.rules_engine import OrderRequest, ValidationResult


def intent(symbol="BTC/USDT", direction="做多", entry=100.0, stop_loss=97.0,
           take_profit=110.0):
    return TradeIntent(
        symbol=symbol, market_type="perpetual", direction=direction,
        entry=entry, stop_loss=stop_loss, take_profit=take_profit,
        strategy="test",
    )


def decision(approved=True, size=200.0, leverage=3.0, reason=None, **kwargs):
    return RiskDecision(
        intent=intent(**kwargs), approved=approved,
        size_usdt=size if approved else None,
        leverage=leverage if approved else None,
        reason=reason,
    )


def order_request(symbol="BTC/USDT", quantity=6.0):
    return OrderRequest(
        client_order_id="agmcis-test-1",
        symbol=symbol,
        market_type=MarketType.PERPETUAL,
        side=OrderSide.BUY,
        position_side=PositionSide.LONG,
        order_type=OrderType.MARKET,
        quantity=quantity,
        reference_price=100.0,
        stop_loss=97.0,
        leverage=3.0,
        size_usdt=200.0,
    )


class FakeRules:
    def __init__(self, ok=True, violations=None, adjustments=None):
        self.result = ValidationResult(
            ok=ok,
            order=order_request() if ok else None,
            violations=list(violations or []),
            adjustments=list(adjustments or []),
        )
        self.calls = []

    def validate(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


class FakeBroker(Broker):
    name = "fake"

    def __init__(self, fill=None, protected=True, close_result=None,
                 submit_error=None, close_error=None):
        self.fill = fill or FillResult(
            ok=True, filled_quantity=6.0, average_price=100.05,
            exchange_order_id="X1",
        )
        self.protected = protected
        self.close_result = close_result or FillResult(
            ok=True, filled_quantity=6.0, average_price=99.9,
        )
        self.submit_error = submit_error
        self.close_error = close_error
        self.submitted = []
        self.closed = []

    def submit_entry(self, order_request, intent, size_usdt, leverage):
        if self.submit_error:
            raise self.submit_error
        self.submitted.append((intent.symbol, size_usdt, leverage))
        return self.fill

    def has_protection(self, symbol):
        return self.protected

    def close_position(self, symbol, price=None, reason=""):
        if self.close_error:
            raise self.close_error
        self.closed.append((symbol, reason))
        return self.close_result

    def get_position(self, symbol):
        return None


class FakeStore:
    """記在記憶體裡的訂單儲存,讓測試不需要資料庫。"""

    def __init__(self):
        self.saved = []
        self.events = []

    def save(self, order, trade_id=None, source="EXEC"):
        self.saved.append((order.client_order_id, order.state.value))
        return len(self.saved)

    def record_event(self, client_order_id, from_state, to_state, reason=None):
        self.events.append((
            client_order_id,
            from_state.value if hasattr(from_state, "value") else from_state,
            to_state.value if hasattr(to_state, "value") else to_state,
            reason,
        ))

    def mark_reconciled(self, client_order_id):
        pass


def build(broker=None, rules=None, store=None):
    return ExecutionEngine(
        broker=broker or FakeBroker(),
        rules_engine=rules or FakeRules(),
        store=store if store is not None else FakeStore(),
    )


class TestTheEngineMakesNoDecisions(unittest.TestCase):

    def test_an_unapproved_decision_is_never_executed(self):
        broker = FakeBroker()
        result = build(broker).execute(
            decision(approved=False, reason="MAX_DAILY_LOSS"),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.status, engine_module.REJECTED_NOT_APPROVED)
        self.assertEqual(broker.submitted, [])

    def test_a_decision_without_size_is_refused_rather_than_guessed(self):
        """執行層不自己算倉位大小。缺了就拒絕,不要憑空生一個出來。"""
        bad = decision()
        bad.size_usdt = None

        result = build().execute(bad)

        self.assertFalse(result.ok)
        self.assertIn("不自己算", result.reason)

    def test_the_size_sent_to_the_broker_is_the_one_risk_decided(self):
        broker = FakeBroker()
        build(broker).execute(decision(size=137.5, leverage=4.0))

        self.assertEqual(broker.submitted, [("BTC/USDT", 137.5, 4.0)])

    def test_trading_rule_violations_stop_the_order_before_submission(self):
        broker = FakeBroker()
        rules = FakeRules(ok=False, violations=["數量低於最小下單量"])

        result = build(broker, rules).execute(decision())

        self.assertFalse(result.ok)
        self.assertEqual(result.status, engine_module.REJECTED_BY_RULES)
        self.assertEqual(broker.submitted, [])
        self.assertIn("最小下單量", result.violations[0])


class TestNakedPositions(unittest.TestCase):
    """
    開倉成功但沒有停損 = 部位存在但沒有虧損上限。
    這不是待辦事項,是必須立刻處理的狀態。
    """

    def test_a_position_without_protection_is_closed_immediately(self):
        broker = FakeBroker(protected=False)

        with patch.object(engine_module, "logger"):
            result = build(broker).execute(decision())

        self.assertFalse(result.ok)
        self.assertEqual(result.status, engine_module.NAKED_POSITION_CLOSED)
        self.assertTrue(result.naked_position_closed)
        self.assertEqual(len(broker.closed), 1)

    def test_the_close_reason_says_why(self):
        broker = FakeBroker(protected=False)

        with patch.object(engine_module, "logger"):
            build(broker).execute(decision())

        self.assertIn("裸倉", broker.closed[0][1])

    def test_a_protected_position_is_left_alone(self):
        broker = FakeBroker(protected=True)
        result = build(broker).execute(decision())

        self.assertTrue(result.ok)
        self.assertEqual(result.status, engine_module.OPENED)
        self.assertEqual(broker.closed, [])

    def test_a_failing_protection_check_is_treated_as_unprotected(self):
        """
        檢查不出來就當作沒有保護。反過來(檢查失敗就假設有保護)
        會讓一個裸倉安靜地留在帳上。
        """
        broker = FakeBroker(protected=True)
        broker.has_protection = MagicMock(side_effect=RuntimeError("查不到"))

        with patch.object(engine_module, "logger"):
            result = build(broker).execute(decision())

        self.assertEqual(result.status, engine_module.NAKED_POSITION_CLOSED)

    def test_a_naked_position_that_cannot_be_closed_is_escalated(self):
        """最糟的情況:有裸倉、又平不掉。必須大聲喊,不能回報成功。"""
        broker = FakeBroker(
            protected=False,
            close_result=FillResult(ok=False, reason="交易所拒絕"),
        )

        with patch.object(engine_module, "logger") as logger:
            result = build(broker).execute(decision())

        self.assertEqual(result.status, engine_module.NAKED_POSITION_STUCK)
        self.assertIn("人工", result.reason)
        logger.critical.assert_called()

    def test_the_sweep_closes_positions_that_lost_their_stop(self):
        """
        開倉時的檢查是主要防線,巡檢是安全網 ——
        停損可能因為別的原因消失(手動改、對帳修正、資料庫問題)。
        """
        broker = FakeBroker()
        positions = [
            {"symbol": "AAA/USDT", "stoploss": 97.0},
            {"symbol": "BBB/USDT", "stoploss": None},
        ]

        with patch.object(engine_module, "logger"):
            summary = build(broker).sweep_naked_positions(positions)

        self.assertEqual(summary["closed"], ["BBB/USDT"])
        self.assertEqual(summary["stuck"], [])

    def test_the_sweep_reports_positions_it_could_not_close(self):
        broker = FakeBroker(close_result=FillResult(ok=False, reason="平不掉"))

        with patch.object(engine_module, "logger") as logger:
            summary = build(broker).sweep_naked_positions(
                [{"symbol": "BBB/USDT", "stoploss": None}],
            )

        self.assertEqual(summary["stuck"], ["BBB/USDT"])
        logger.critical.assert_called()


class TestUnknownStatesAreNotRetried(unittest.TestCase):
    """
    送單過程炸掉時,系統**不知道**交易所收到了什麼。
    直接重送可能開出第二張倉位。
    """

    def test_a_submit_exception_lands_in_unknown_not_rejected(self):
        broker = FakeBroker(submit_error=TimeoutError("連線逾時"))

        with patch.object(engine_module, "logger"):
            result = build(broker).execute(decision())

        self.assertFalse(result.ok)
        self.assertIs(result.order.state, OrderState.UNKNOWN)
        self.assertIn("對帳", result.reason)

    def test_an_unknown_order_may_not_be_resubmitted(self):
        order = Order(client_order_id="x", symbol="BTC/USDT",
                      market_type="perpetual", side="buy",
                      order_type="market", quantity=1.0,
                      state=OrderState.UNKNOWN)

        self.assertFalse(sm.can_resubmit(order))

    def test_an_explicit_rejection_is_different_from_unknown(self):
        """交易所明確說「不收」時狀態是 REJECTED,那是可以確定的結果。"""
        broker = FakeBroker(fill=FillResult(ok=False, reason="餘額不足"))

        result = build(broker).execute(decision())

        self.assertIs(result.order.state, OrderState.REJECTED)
        self.assertEqual(result.status, engine_module.SUBMIT_FAILED)


class TestStateMachine(unittest.TestCase):

    def _order(self, state=OrderState.CREATED):
        return Order(client_order_id="x", symbol="BTC/USDT",
                     market_type="perpetual", side="buy",
                     order_type="market", quantity=1.0, state=state)

    def test_the_happy_path_is_allowed(self):
        order = self._order()
        for target in (OrderState.VALIDATING, OrderState.RISK_CHECK,
                       OrderState.SUBMITTING, OrderState.ACCEPTED,
                       OrderState.FILLED, OrderState.PROTECTED,
                       OrderState.CLOSING, OrderState.CLOSED):
            with self.subTest(target=target):
                sm.transition(order, target)

        self.assertIs(order.state, OrderState.CLOSED)

    def test_skipping_straight_to_filled_is_rejected(self):
        """把一張剛建立的單當成已成交,就是憑空多一個部位。"""
        with self.assertRaises(sm.IllegalTransition):
            sm.transition(self._order(), OrderState.FILLED)

    def test_a_terminal_state_has_no_next_step(self):
        for state in (OrderState.CLOSED, OrderState.REJECTED,
                      OrderState.CANCELLED, OrderState.FAILED):
            with self.subTest(state=state):
                with self.assertRaises(sm.IllegalTransition):
                    sm.transition(self._order(state), OrderState.CLOSING)

    def test_transitioning_to_the_same_state_is_rejected(self):
        """同狀態轉移通常代表呼叫端搞不清楚自己在哪一步。"""
        with self.assertRaises(sm.IllegalTransition):
            sm.transition(self._order(OrderState.ACCEPTED), OrderState.ACCEPTED)

    def test_timeout_is_a_legal_outcome_of_submitting(self):
        """逾時不是程式錯誤,是送單的正常結果之一。"""
        self.assertTrue(sm.can_transition(OrderState.SUBMITTING, OrderState.TIMEOUT))

    def test_reconciliation_can_resolve_an_unknown_order(self):
        order = self._order(OrderState.UNKNOWN)
        sm.transition(order, OrderState.FILLED, reason="對帳後確認已成交")

        self.assertIs(order.state, OrderState.FILLED)

    def test_filled_without_protection_is_reported_as_naked(self):
        self.assertTrue(sm.is_naked(self._order(OrderState.FILLED)))
        self.assertTrue(sm.is_naked(self._order(OrderState.PARTIALLY_FILLED)))
        self.assertFalse(sm.is_naked(self._order(OrderState.PROTECTED)))

    def test_every_state_has_an_entry_in_the_transition_table(self):
        """漏掉一個狀態會讓它變成死路,而且是在執行當下才發現。"""
        for state in OrderState:
            with self.subTest(state=state):
                self.assertIn(state, sm.TRANSITIONS)


class TestClosing(unittest.TestCase):

    def test_a_successful_close_reports_the_fill_price(self):
        result = build().close("BTC/USDT", reason="策略出場")

        self.assertTrue(result.ok)
        self.assertEqual(result.fill_price, 99.9)

    def test_nothing_to_close_is_not_an_exception(self):
        """另一條路徑已經平掉是很常見的情況,不是錯誤。"""
        broker = FakeBroker(close_result=FillResult(ok=False, reason="沒有持倉"))

        with patch.object(engine_module, "logger"):
            result = build(broker).close("BTC/USDT")

        self.assertFalse(result.ok)
        self.assertEqual(result.status, engine_module.CLOSE_FAILED)

    def test_a_close_exception_does_not_propagate(self):
        broker = FakeBroker(close_error=RuntimeError("斷線"))

        with patch.object(engine_module, "logger"):
            result = build(broker).close("BTC/USDT")

        self.assertFalse(result.ok)
        self.assertIn("斷線", result.reason)


class TestNoLiveBrokerExists(unittest.TestCase):
    """
    實單能力必須是可以整個拔掉的。Phase 17 的 Safety Gate 通過之前,
    系統裡不該有任何一條路徑能送出真實訂單 —— 不是靠設定擋住,
    是靠沒有那個實作。
    """

    def test_there_is_no_live_broker_implementation_yet(self):
        from agmcis.execution import broker as broker_module

        live = [
            name for name in dir(broker_module)
            if "live" in name.lower() or "real" in name.lower()
        ]

        self.assertEqual(live, [])

    def test_the_paper_broker_is_not_marked_live(self):
        from agmcis.execution.broker import PaperBroker

        self.assertFalse(PaperBroker.is_live)


class TestOrdersArePersisted(unittest.TestCase):
    """
    Phase 12 的 Order 只活在記憶體裡。程式重啟時 UNKNOWN 狀態的訂單就消失了 ——
    而那正是最需要被記住的狀態。
    """

    def test_every_transition_is_written_down(self):
        store = FakeStore()
        build(store=store).execute(decision())

        states = [state for _, state in store.saved]
        self.assertEqual(
            states,
            ["validating", "risk_check", "submitting", "accepted",
             "filled", "protected"],
        )

    def test_the_event_trail_records_where_it_came_from(self):
        """
        只有最終狀態是不夠的。一張最後變成 CLOSED 的單,
        是「正常成交後平倉」還是「裸倉被緊急平掉」,差別很大。
        """
        store = FakeStore()
        broker = FakeBroker(protected=False)

        with patch.object(engine_module, "logger"):
            build(broker, store=store).execute(decision())

        closing = [e for e in store.events if e[2] == "closing"]
        self.assertTrue(closing)
        self.assertIn("裸倉", closing[0][3])

    def test_an_unknown_order_is_persisted_before_the_caller_sees_it(self):
        """
        最需要被記住的就是這個狀態。沒寫下來就等於放棄對帳。
        """
        store = FakeStore()
        broker = FakeBroker(submit_error=TimeoutError("逾時"))

        with patch.object(engine_module, "logger"):
            build(broker, store=store).execute(decision())

        self.assertEqual(store.saved[-1][1], "unknown")

    def test_a_persistence_failure_is_loud_but_does_not_lose_the_order(self):
        """
        寫不進資料庫時,已經送出去的單不能消失,但必須大聲說 ——
        這時資料庫的狀態已經落後於真實狀態了。
        """
        store = FakeStore()
        store.save = MagicMock(side_effect=RuntimeError("資料庫掛了"))

        with patch.object(engine_module, "logger") as logger:
            result = build(store=store).execute(decision())

        self.assertTrue(result.ok)
        logger.critical.assert_called()


if __name__ == "__main__":
    unittest.main()
