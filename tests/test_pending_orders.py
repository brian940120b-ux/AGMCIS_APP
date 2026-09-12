"""
掛單與加倉 / 反手(Master Prompt 第十三 / 十四節)。

訂單型別列舉早就齊全了,但一張「掛在 98 等它跌下來」的單需要的
不只是一個型別欄位 —— 它需要一個會在價格到達時把它變成成交的東西。

這組測試最在意三件事:
  一、掛單不會因為送出去就被當成成交(那是最容易系統性高估的地方)。
  二、成交價是限價不是現價。
  三、反手先平再開,而且平不掉就不開。
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.core.enums import Direction, OrderSide, OrderType, PositionSide
from agmcis.core.models import RiskDecision, TradeIntent
from agmcis.execution import pending as pending_module
from agmcis.execution.broker import Broker, FillResult
from agmcis.execution.engine import ExecutionEngine
from agmcis.execution.rules_engine import OrderRequest, ValidationResult

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def order(direction="做多", order_type="limit", trigger=98.0, ttl=4.0):
    return pending_module.PendingOrder(
        client_order_id="p-1", symbol="BTC/USDT", direction=direction,
        order_type=order_type, trigger_price=trigger,
        quantity=10.0, size_usdt=200.0, leverage=3.0,
        stop_loss=95.0, take_profit=110.0,
        created_at=NOW,
        expires_at=NOW + timedelta(hours=ttl) if ttl else None,
    )


class TestTriggering(unittest.TestCase):

    def test_a_long_limit_fills_when_price_drops_to_it(self):
        self.assertTrue(order().triggered(price=97.0))

    def test_a_long_limit_does_not_fill_above_it(self):
        self.assertFalse(order().triggered(price=99.0))

    def test_a_short_limit_fills_when_price_rises_to_it(self):
        self.assertTrue(
            order(direction="做空", trigger=102.0).triggered(price=103.0)
        )

    def test_a_long_stop_fills_on_a_breakout_upwards(self):
        """停損進場掛在現價上方:突破才進場,與限價買相反。"""
        entry = order(order_type="stop", trigger=105.0)

        self.assertTrue(entry.triggered(price=106.0))
        self.assertFalse(entry.triggered(price=104.0))

    def test_an_intrabar_touch_counts(self):
        """
        輪詢間隔內價格可能碰到又離開。只看當下的價格會漏掉
        真的成交過的機會 —— 與 position_monitor 同一個理由。
        """
        self.assertTrue(order().triggered(price=99.0, high=99.5, low=97.5))

    def test_the_range_takes_priority_over_the_point_price(self):
        self.assertFalse(order().triggered(price=97.0, high=100.0, low=99.0))

    def test_an_unknown_type_is_an_error_not_a_silent_no(self):
        entry = order(order_type="market")

        with self.assertRaises(ValueError):
            entry.triggered(price=97.0)


class TestFillPrice(unittest.TestCase):

    def test_a_limit_fills_at_the_limit_not_the_current_price(self):
        """
        價格跌到 97 時,掛在 98 的買單成交在 98。用 97 記帳會
        系統性高估 —— 而那是往樂觀的方向。
        """
        self.assertEqual(order().fill_price(), 98.0)

    def test_a_stop_pays_slippage_because_it_becomes_a_market_order(self):
        from agmcis.backtest.costs import CostModel

        costs = CostModel(slippage_pct=0.01, spread_pct=0.0)
        entry = order(order_type="stop", trigger=105.0)

        self.assertGreater(entry.fill_price(costs=costs), 105.0)


class TestExpiry(unittest.TestCase):
    """
    一張掛了三天的單,當初的訊號早就過期了,但它還是會在某個半夜成交。
    """

    def test_an_order_expires(self):
        self.assertTrue(order().is_expired(NOW + timedelta(hours=5)))

    def test_a_fresh_order_does_not(self):
        self.assertFalse(order().is_expired(NOW + timedelta(hours=1)))

    def test_an_order_without_a_ttl_never_expires(self):
        self.assertFalse(order(ttl=None).is_expired(NOW + timedelta(days=30)))

    def test_an_expired_order_is_not_filled_even_if_it_triggers(self):
        """當初的訊號已經不成立了。"""
        book = pending_module.PendingBook(store=object())
        book.add(order())

        results = book.check(
            price_of=lambda s: 97.0,
            fill=lambda p, price: True,
            now=NOW + timedelta(hours=5),
        )

        self.assertEqual(results[0].status, pending_module.EXPIRED)
        self.assertEqual(book.all(), [])


class TestTheBook(unittest.TestCase):

    def _book(self):
        book = pending_module.PendingBook(store=object())
        book.add(order())
        return book

    def test_a_triggered_order_is_filled_and_removed(self):
        book = self._book()
        filled = []

        results = book.check(
            price_of=lambda s: 97.0,
            fill=lambda p, price: filled.append(price) or True,
            now=NOW,
        )

        self.assertEqual(results[0].status, pending_module.FILLED)
        self.assertEqual(filled, [98.0])
        self.assertEqual(book.all(), [])

    def test_an_untriggered_order_stays(self):
        book = self._book()

        results = book.check(price_of=lambda s: 99.0, now=NOW)

        self.assertEqual(results[0].status, pending_module.STILL_WAITING)
        self.assertEqual(len(book.all()), 1)

    def test_a_rejected_fill_keeps_the_order(self):
        book = self._book()

        results = book.check(
            price_of=lambda s: 97.0, fill=lambda p, price: False, now=NOW,
        )

        self.assertEqual(results[0].status, pending_module.STILL_WAITING)
        self.assertEqual(len(book.all()), 1)

    def test_a_missing_price_skips_rather_than_guesses(self):
        book = self._book()

        results = book.check(price_of=lambda s: None, now=NOW)

        self.assertEqual(results[0].reason, "取不到現價")
        self.assertEqual(len(book.all()), 1)

    def test_one_bad_symbol_does_not_stop_the_round(self):
        """
        一輪在中間爆掉會讓後面所有掛單都沒有人管。
        """
        book = pending_module.PendingBook(store=object())
        first = order()
        second = order()
        second.client_order_id = "p-2"
        second.symbol = "ETH/USDT"
        book.add(first)
        book.add(second)

        def price_of(symbol):
            if symbol == "BTC/USDT":
                raise RuntimeError("取價失敗")
            return 97.0

        with patch.object(pending_module, "logger"):
            results = book.check(
                price_of=price_of, fill=lambda p, price: True, now=NOW,
            )

        self.assertEqual(len(results), 2)
        self.assertIn(pending_module.FILLED, [r.status for r in results])

    def test_cancelling_removes_it(self):
        book = self._book()

        result = book.cancel("p-1", reason="訊號失效")

        self.assertEqual(result.status, pending_module.CANCELLED)
        self.assertEqual(book.all(), [])

    def test_cancelling_something_that_is_not_there_is_not_an_error(self):
        self.assertIsNone(self._book().cancel("nope"))


# ---------------- 執行層 ----------------

def intent(direction="做多", **overrides):
    base = dict(symbol="BTC/USDT", market_type="perpetual", direction=direction,
                entry=100.0, stop_loss=97.0 if direction == "做多" else 103.0,
                take_profit=110.0 if direction == "做多" else 90.0)
    base.update(overrides)
    return TradeIntent(**base)


def decision(direction="做多"):
    return RiskDecision(
        intent=intent(direction), approved=True, size_usdt=200.0, leverage=3.0,
    )


class FakeRules:
    def __init__(self, order_type=OrderType.MARKET, price=None):
        self.order_type = order_type
        self.price = price

    def validate(self, intent, size_usdt, leverage, order_type=None, context=None):
        kind = order_type or self.order_type
        return ValidationResult(
            ok=True,
            order=OrderRequest(
                client_order_id="x-1", symbol=intent.symbol,
                market_type="perpetual",
                side=OrderSide.BUY if intent.direction is Direction.LONG
                     else OrderSide.SELL,
                position_side=PositionSide.LONG
                     if intent.direction is Direction.LONG else PositionSide.SHORT,
                order_type=kind, quantity=6.0,
                reference_price=100.0, price=self.price,
                stop_loss=intent.stop_loss, leverage=3.0, size_usdt=200.0,
            ),
        )


class FakeBroker(Broker):
    name = "fake"

    def __init__(self, close_ok=True):
        self.close_ok = close_ok
        self.submitted = []
        self.closed = []

    def submit_entry(self, order_request, intent, size_usdt, leverage):
        self.submitted.append(intent.direction.value)
        return FillResult(
            ok=True, filled_quantity=6.0, requested_quantity=6.0,
            average_price=100.0, exchange_order_id="X1",
        )

    def has_protection(self, symbol):
        return True

    def close_position(self, symbol, price=None, reason=""):
        self.closed.append(reason)
        return FillResult(
            ok=self.close_ok, filled_quantity=6.0, average_price=99.0,
            reason=None if self.close_ok else "交易所拒絕",
        )

    def cancel_remainder(self, order):
        return True

    def get_position(self, symbol):
        return None


class FakeStore:
    def save(self, order, trade_id=None, source="EXEC"):
        return 1

    def record_event(self, *args, **kwargs):
        pass

    def mark_reconciled(self, client_order_id):
        pass


def engine(broker=None, rules=None):
    return ExecutionEngine(
        broker=broker or FakeBroker(),
        rules_engine=rules or FakeRules(),
        store=FakeStore(),
        pause_file="/tmp/agmcis-test-pending-pause.flag",
    )


class TestNonMarketOrdersDoNotOpenAPosition(unittest.TestCase):
    """
    把掛單當成成交,是回測與模擬盤最容易系統性高估的地方之一。
    """

    def setUp(self):
        self.book = pending_module.PendingBook(store=object())
        pending_module.set_book(self.book)
        self.addCleanup(pending_module.set_book, None)

    def test_a_limit_order_rests_instead_of_filling(self):
        broker = FakeBroker()

        with patch("agmcis.execution.engine.logger"):
            result = engine(
                broker, rules=FakeRules(OrderType.LIMIT, price=98.0),
            ).execute(decision(), order_type=OrderType.LIMIT)

        self.assertEqual(result.status, "PENDING")
        self.assertEqual(broker.submitted, [], "掛單不該送市價單")
        self.assertEqual(len(self.book.all()), 1)

    def test_a_pending_result_is_ok_but_has_no_position(self):
        """
        ok=True 代表訂單建立成功,**不代表有部位** ——
        沒有部位就沒有停損要檢查。
        """
        with patch("agmcis.execution.engine.logger"):
            result = engine(
                rules=FakeRules(OrderType.LIMIT, price=98.0),
            ).execute(decision(), order_type=OrderType.LIMIT)

        self.assertTrue(result.ok)
        self.assertNotEqual(result.status, "OPENED")

    def test_a_market_order_still_fills_immediately(self):
        broker = FakeBroker()

        result = engine(broker).execute(decision())

        self.assertEqual(result.status, "OPENED")
        self.assertEqual(len(self.book.all()), 0)


class TestAddPosition(unittest.TestCase):

    def test_adding_in_the_same_direction_works(self):
        broker = FakeBroker()
        position = {"symbol": "BTC/USDT", "signal": "做多"}

        result = engine(broker).add_to_position(decision("做多"), position)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "ADDED")

    def test_adding_in_the_opposite_direction_is_refused(self):
        """
        反向的「加倉」是反手。兩者的風險完全不同,
        不該用同一個入口。
        """
        position = {"symbol": "BTC/USDT", "signal": "做空"}

        result = engine().add_to_position(decision("做多"), position)

        self.assertFalse(result.ok)
        self.assertIn("reverse_position", result.reason)

    def test_an_unreadable_position_direction_is_refused(self):
        position = {"symbol": "BTC/USDT", "signal": "???"}

        result = engine().add_to_position(decision("做多"), position)

        self.assertFalse(result.ok)


class TestReversePosition(unittest.TestCase):

    def test_it_closes_before_it_opens(self):
        broker = FakeBroker()
        position = {"symbol": "BTC/USDT", "signal": "做空"}

        with patch("agmcis.execution.engine.logger"):
            result = engine(broker).reverse_position(decision("做多"), position)

        self.assertTrue(result.ok)
        self.assertEqual(result.status, "REVERSED")
        self.assertEqual(len(broker.closed), 1)
        self.assertEqual(broker.submitted, ["做多"])

    def test_a_failed_close_means_no_new_position(self):
        """
        先開反向在單向持倉模式下會被拒絕,在雙向持倉模式下會同時
        持有多空 —— 兩者都不是「反手」的意思。
        """
        broker = FakeBroker(close_ok=False)
        position = {"symbol": "BTC/USDT", "signal": "做空"}

        with patch("agmcis.execution.engine.logger"):
            result = engine(broker).reverse_position(decision("做多"), position)

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "REVERSE_CLOSE_FAILED")
        self.assertEqual(broker.submitted, [], "平不掉就不開反向")

    def test_reversing_into_the_same_direction_is_refused(self):
        position = {"symbol": "BTC/USDT", "signal": "做多"}

        result = engine().reverse_position(decision("做多"), position)

        self.assertFalse(result.ok)
        self.assertIn("add_to_position", result.reason)


if __name__ == "__main__":
    unittest.main()
