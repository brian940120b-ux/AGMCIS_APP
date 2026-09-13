"""
LiveBroker(第十八 / 三十二 / 四十六 / 九十四節)。

## 這一組測試的方向

實單的錯誤不是「算錯一個數字」,是「在不知道發生什麼的情況下
做了一件不可逆的事」。所以每一個測試問的都是同一句話:

    這裡壞掉的時候,它往哪一邊倒?

安全的方向是固定的:
  * 不知道多大 → 不送
  * 不知道有沒有停損 → 當作沒有
  * 不知道部位方向 → 不動它
  * 不知道訂單狀態 → 說不知道,不要猜一個樂觀的

模擬盤驗不到這些 —— PaperBroker 寫一個資料庫欄位不會失敗。
"""
import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.core.enums import OrderSide, OrderType, PositionSide
from agmcis.execution import live_broker as live_broker_module
from agmcis.execution.broker import Broker
from agmcis.execution.live_broker import LiveBroker


class FakeAdapter:
    """記下被呼叫了什麼。不連線,所以測試不需要金鑰也不需要網路。"""

    name = "fake"

    def __init__(self, positions=None, open_orders=None, order=None, fail=None,
                 history=None):
        self.calls = []
        self.positions = list(positions or [])
        self.open_orders = list(open_orders or [])
        self.order = dict(order or {})
        self.history = list(history or [])
        self.fail = set(fail or ())

    def _record(self, what, **kwargs):
        self.calls.append((what, kwargs))
        if what in self.fail:
            raise RuntimeError(f"{what} 故意失敗")

    def set_leverage(self, leverage, symbol, market_type=None):
        self._record("set_leverage", leverage=leverage, symbol=symbol)

    def create_order(self, **kwargs):
        self._record("create_order", **kwargs)
        return dict(self.order)

    def cancel_order(self, order_id, symbol, market_type=None):
        self._record("cancel_order", order_id=order_id, symbol=symbol)
        return {}

    def get_order(self, order_id, symbol, market_type=None, params=None):
        self._record("get_order", order_id=order_id, symbol=symbol,
                     params=params)
        return dict(self.order)

    def get_open_orders(self, symbol=None, market_type=None):
        self._record("get_open_orders", symbol=symbol)
        return list(self.open_orders)

    def get_order_history(self, symbol=None, since=None, limit=None,
                          market_type=None):
        self._record("get_order_history", symbol=symbol, since=since)
        return list(self.history)

    def get_positions(self, symbols=None):
        self._record("get_positions", symbols=symbols)
        return list(self.positions)

    def names(self):
        return [name for name, _ in self.calls]

    def kwargs_of(self, name):
        return [kw for n, kw in self.calls if n == name]


class Intent:
    def __init__(self, symbol="BTC-USDT"):
        self.symbol = symbol


class Request:
    def __init__(self, quantity=0.01, price=None, reference_price=50000.0,
                 position_side=PositionSide.LONG, side=OrderSide.BUY):
        self.client_order_id = "cid-1"
        self.side = side
        self.position_side = position_side
        self.order_type = OrderType.MARKET
        self.quantity = quantity
        self.price = price
        self.reference_price = reference_price

    @property
    def notional(self):
        price = self.price if self.price is not None else self.reference_price
        return None if price is None else self.quantity * price


LONG = {"symbol": "BTC-USDT", "side": "long", "contracts": 0.01}
SHORT = {"symbol": "BTC-USDT", "side": "short", "contracts": 0.01}
FILLED = {"id": "ex-1", "filled": 0.01, "average": 50000.0, "status": "closed"}


def broker(adapter, cap=1e9):
    return LiveBroker(adapter=adapter, notional_cap=cap)


class TestItIsWhatItSaysItIs(unittest.TestCase):

    def test_it_is_a_broker_and_it_says_it_is_live(self):
        self.assertTrue(issubclass(LiveBroker, Broker))
        self.assertEqual(LiveBroker.name, "live")
        self.assertIs(LiveBroker.is_live, True)

    def test_it_implements_every_broker_method(self):
        """
        少實作一個方法,Execution Engine 會在實盤第一次走到那條路徑時
        炸掉 NotImplementedError —— 而那可能是緊急平倉。
        """
        missing = [
            name for name in dir(Broker)
            if not name.startswith("_")
            and callable(getattr(Broker, name))
            and getattr(LiveBroker, name) is getattr(Broker, name)
        ]

        self.assertEqual(missing, [])


class TestTheNotionalCap(unittest.TestCase):
    """
    最後一道上限。Risk Engine 已經擋過一次,這裡重複擋是刻意的:
    上游被繞過、被改壞、被新的呼叫路徑跳過時,這裡還在。
    """

    def test_an_unreadable_cap_blocks_the_order(self):
        adapter = FakeAdapter()
        live = LiveBroker(adapter=adapter, notional_cap=None)
        live._max_notional = lambda: None

        fill = live.submit_entry(Request(), Intent(), 100.0, 5)

        self.assertFalse(fill.ok)
        self.assertNotIn("create_order", adapter.names())

    def test_an_order_over_the_cap_is_blocked_before_any_api_call(self):
        adapter = FakeAdapter()

        fill = broker(adapter, cap=50.0).submit_entry(
            Request(quantity=1.0), Intent(), 100.0, 5,
        )

        self.assertFalse(fill.ok)
        self.assertEqual(adapter.calls, [])

    def test_an_order_whose_size_cannot_be_computed_is_blocked(self):
        """不知道多大就不下單。"""
        request = Request()
        request.reference_price = None
        adapter = FakeAdapter()

        fill = broker(adapter).submit_entry(request, Intent(), 100.0, 5)

        self.assertFalse(fill.ok)
        self.assertEqual(adapter.calls, [])

    def test_a_zero_or_negative_quantity_is_blocked(self):
        for quantity in (0, -1, None):
            with self.subTest(quantity=quantity):
                adapter = FakeAdapter()
                fill = broker(adapter).submit_entry(
                    Request(quantity=quantity), Intent(), 100.0, 5,
                )
                self.assertFalse(fill.ok)
                self.assertEqual(adapter.calls, [])


class TestSubmitEntry(unittest.TestCase):

    def test_it_sends_the_quantity_the_rules_engine_validated(self):
        """
        送的是 order_request.quantity,不是 size_usdt × leverage 重算的。
        重算會讓送出去的量與上游驗證過的量不一致,而上游驗證過的
        才是「送得出去」的那一個。
        """
        adapter = FakeAdapter(order=FILLED)

        broker(adapter).submit_entry(Request(quantity=0.01), Intent(), 999.0, 5)

        self.assertEqual(adapter.kwargs_of("create_order")[0]["quantity"], 0.01)

    def test_it_sets_leverage_before_sending(self):
        adapter = FakeAdapter(order=FILLED)

        broker(adapter).submit_entry(Request(), Intent(), 100.0, 7)

        names = adapter.names()
        self.assertLess(names.index("set_leverage"), names.index("create_order"))
        self.assertEqual(adapter.kwargs_of("set_leverage")[0]["leverage"], 7.0)

    def test_a_failed_leverage_call_stops_the_order(self):
        """
        用交易所帳戶上一次的槓桿去開倉,倉位大小就不是我們算的那個。
        """
        adapter = FakeAdapter(order=FILLED, fail={"set_leverage"})

        fill = broker(adapter).submit_entry(Request(), Intent(), 100.0, 5)

        self.assertFalse(fill.ok)
        self.assertNotIn("create_order", adapter.names())

    def test_it_passes_the_client_order_id_through(self):
        """冪等鍵。重送同一個 id 交易所會拒絕第二次。"""
        adapter = FakeAdapter(order=FILLED)

        broker(adapter).submit_entry(Request(), Intent(), 100.0, 5)

        self.assertEqual(
            adapter.kwargs_of("create_order")[0]["client_order_id"], "cid-1",
        )

    def test_a_response_without_a_filled_field_is_not_a_zero_fill(self):
        """
        交易所回了東西但沒說成交多少。那是「狀態不明」,
        不是「成交 0」—— 兩者的後續處理完全相反。
        """
        adapter = FakeAdapter(order={"id": "ex-1", "status": "open"})

        fill = broker(adapter).submit_entry(Request(), Intent(), 100.0, 5)

        self.assertFalse(fill.ok)
        self.assertIn("狀態不明", fill.reason)
        self.assertEqual(fill.exchange_order_id, "ex-1")

    def test_a_partial_fill_carries_the_requested_quantity(self):
        """
        沒帶 requested_quantity 的話 is_partial() 永遠回 False,
        剩餘量就沒有人去撤。
        """
        adapter = FakeAdapter(order={"id": "ex-1", "filled": 0.004,
                                     "average": 50000.0})

        fill = broker(adapter).submit_entry(
            Request(quantity=0.01), Intent(), 100.0, 5,
        )

        self.assertTrue(fill.is_filled)
        self.assertTrue(fill.is_partial())
        self.assertEqual(fill.requested_quantity, 0.01)

    def test_an_exception_is_not_swallowed(self):
        """
        送單之後連線斷掉,交易所可能已經收到了。
        在這裡吞掉例外並回 ok=False,等於宣稱「沒有送出去」——
        Execution Engine 要看到例外才會標成 UNKNOWN 交給對帳。
        """
        adapter = FakeAdapter(order=FILLED, fail={"create_order"})

        with self.assertRaises(RuntimeError):
            broker(adapter).submit_entry(Request(), Intent(), 100.0, 5)

    def test_one_way_mode_does_not_send_a_position_side(self):
        adapter = FakeAdapter(order=FILLED)

        broker(adapter).submit_entry(
            Request(position_side=PositionSide.BOTH), Intent(), 100.0, 5,
        )

        self.assertIsNone(adapter.kwargs_of("create_order")[0]["params"])

    def test_hedge_mode_sends_the_position_side(self):
        """帶錯會讓平倉單變成反手開倉。"""
        adapter = FakeAdapter(order=FILLED)

        broker(adapter).submit_entry(
            Request(position_side=PositionSide.SHORT), Intent(), 100.0, 5,
        )

        self.assertEqual(
            adapter.kwargs_of("create_order")[0]["params"],
            {"positionSide": "SHORT"},
        )


class TestProtection(unittest.TestCase):
    """第十八節:任何持倉都必須有停損。"""

    def test_a_failed_query_counts_as_no_protection(self):
        """
        「不知道」必須等於「沒有保護」。重掛一張已經存在的停損,
        比放著一個可能沒有保護的部位安全得多。
        """
        adapter = FakeAdapter(fail={"get_open_orders"})

        self.assertFalse(broker(adapter).has_protection("BTC-USDT"))

    def test_a_reduce_only_stop_counts_as_protection(self):
        adapter = FakeAdapter(open_orders=[
            {"id": "s", "type": "stop_market", "reduceOnly": True},
        ])

        self.assertTrue(broker(adapter).has_protection("BTC-USDT"))

    def test_a_stop_that_is_not_reduce_only_does_not_count(self):
        """它觸發時會反手開倉,不是平倉。那不是保護。"""
        adapter = FakeAdapter(open_orders=[
            {"id": "s", "type": "stop_market", "reduceOnly": False},
        ])

        self.assertFalse(broker(adapter).has_protection("BTC-USDT"))

    def test_a_limit_order_is_not_protection(self):
        adapter = FakeAdapter(open_orders=[
            {"id": "l", "type": "limit", "reduceOnly": True},
        ])

        self.assertFalse(broker(adapter).has_protection("BTC-USDT"))

    def test_no_position_means_no_stop_is_placed(self):
        adapter = FakeAdapter(positions=[])

        self.assertFalse(broker(adapter).ensure_stop_loss("BTC-USDT", 49000.0))
        self.assertNotIn("create_order", adapter.names())

    def test_a_missing_stop_price_is_refused(self):
        for price in (None, 0, -1):
            with self.subTest(price=price):
                adapter = FakeAdapter(positions=[dict(LONG)])
                self.assertFalse(
                    broker(adapter).ensure_stop_loss("BTC-USDT", price)
                )

    def test_it_cancels_the_old_stop_before_placing_the_new_one(self):
        """
        順序反過來會有一瞬間掛著兩張,兩張都觸發就變成反向開倉。
        """
        adapter = FakeAdapter(
            positions=[dict(LONG)],
            open_orders=[{"id": "old", "type": "stop_market",
                          "reduceOnly": True}],
            order=FILLED,
        )

        broker(adapter).ensure_stop_loss("BTC-USDT", 49000.0)

        names = adapter.names()
        self.assertLess(names.index("cancel_order"), names.index("create_order"))

    def test_the_new_stop_is_reduce_only_and_on_the_exit_side(self):
        adapter = FakeAdapter(positions=[dict(LONG)], order=FILLED)

        broker(adapter).ensure_stop_loss("BTC-USDT", 49000.0)

        sent = adapter.kwargs_of("create_order")[0]
        self.assertIs(sent["side"], OrderSide.SELL)
        self.assertIs(sent["order_type"], OrderType.STOP_MARKET)
        self.assertTrue(sent["reduce_only"])
        self.assertEqual(sent["params"], {"stopPrice": 49000.0})

    def test_a_hedged_position_gets_a_position_side_on_its_stop(self):
        """
        進場單帶了 positionSide、出場單沒帶,在雙向持倉就是
        「平倉單變成反手開倉」的那條路。
        """
        adapter = FakeAdapter(
            positions=[dict(LONG, hedged=True)], order=FILLED,
        )

        broker(adapter).ensure_stop_loss("BTC-USDT", 49000.0)

        params = adapter.kwargs_of("create_order")[0]["params"]
        self.assertEqual(params["positionSide"], "LONG")
        self.assertEqual(params["stopPrice"], 49000.0)

    def test_a_one_way_position_gets_no_position_side(self):
        """
        單向持倉的帳戶送 positionSide 會被交易所拒絕。
        猜不出來的時候要往「不送」倒:少一個參數是被拒絕,
        帶錯一個參數是開了一張反向的單。
        """
        adapter = FakeAdapter(positions=[dict(LONG)], order=FILLED)

        broker(adapter).ensure_stop_loss("BTC-USDT", 49000.0)

        self.assertNotIn(
            "positionSide", adapter.kwargs_of("create_order")[0]["params"],
        )

    def test_a_short_position_stops_out_with_a_buy(self):
        adapter = FakeAdapter(positions=[dict(SHORT)], order=FILLED)

        broker(adapter).ensure_stop_loss("BTC-USDT", 51000.0)

        self.assertIs(adapter.kwargs_of("create_order")[0]["side"], OrderSide.BUY)

    def test_a_failed_stop_order_returns_false(self):
        """不假裝掛上了。呼叫端會走第十八節的緊急流程。"""
        adapter = FakeAdapter(positions=[dict(LONG)], fail={"create_order"})

        self.assertFalse(broker(adapter).ensure_stop_loss("BTC-USDT", 49000.0))

    def test_success_only_means_the_request_was_accepted(self):
        """
        回 True 之後仍然要用 has_protection() 確認一次 ——
        交易所收下請求、回 200、然後因為觸發價越過現價而撤掉,
        是真的會發生的。這個測試把那兩件事分開:
        掛單成功,但交易所那邊查不到 → 沒有保護。
        """
        adapter = FakeAdapter(positions=[dict(LONG)], order=FILLED,
                              open_orders=[])
        live = broker(adapter)

        self.assertTrue(live.ensure_stop_loss("BTC-USDT", 49000.0))
        self.assertFalse(live.has_protection("BTC-USDT"))


class TestClosing(unittest.TestCase):

    def test_it_closes_a_long_with_a_reduce_only_sell(self):
        adapter = FakeAdapter(positions=[dict(LONG)], order=FILLED)

        result = broker(adapter).close_position("BTC-USDT", reason="TEST")

        sent = adapter.kwargs_of("create_order")[-1]
        self.assertTrue(result.ok)
        self.assertIs(sent["side"], OrderSide.SELL)
        self.assertTrue(sent["reduce_only"])

    def test_a_hedged_close_carries_the_position_side(self):
        adapter = FakeAdapter(
            positions=[dict(SHORT, hedged=True)], order=FILLED,
        )

        broker(adapter).close_position("BTC-USDT")

        params = adapter.kwargs_of("create_order")[-1]["params"]
        self.assertEqual(params["positionSide"], "SHORT")

    def test_a_hedged_position_with_no_direction_gets_no_position_side(self):
        """
        hedged=True 但方向欄位是空的 —— 那是「不知道」,不是「LONG」。
        不過這條路走不到 create_order:方向不明本來就不平倉。
        """
        live = broker(FakeAdapter())

        self.assertIsNone(live._exit_params({"hedged": True, "side": ""}))

    def test_an_unknown_direction_is_never_guessed(self):
        """猜錯的代價是把倉位開成兩倍,不是平掉。"""
        adapter = FakeAdapter(positions=[{"symbol": "BTC-USDT", "side": "",
                                          "contracts": 0.01}])

        result = broker(adapter).close_position("BTC-USDT")

        self.assertFalse(result.ok)
        self.assertNotIn("create_order", adapter.names())

    def test_no_position_is_not_an_error_but_is_not_a_fill_either(self):
        adapter = FakeAdapter(positions=[])

        result = broker(adapter).close_position("BTC-USDT")

        self.assertFalse(result.ok)
        self.assertNotIn("create_order", adapter.names())

    def test_it_cancels_the_stop_before_closing(self):
        """留著的話停損會在平倉之後變成反向開倉。"""
        adapter = FakeAdapter(
            positions=[dict(LONG)],
            open_orders=[{"id": "s", "type": "stop_market",
                          "reduceOnly": True}],
            order=FILLED,
        )

        broker(adapter).close_position("BTC-USDT")

        names = adapter.names()
        self.assertLess(names.index("cancel_order"), names.index("create_order"))

    def test_closing_is_not_limited_by_the_notional_cap(self):
        """
        上限的目的是擋住「開太大」。一個因為超過上限而平不掉的部位,
        是這道上限能造成的最壞結果。
        """
        adapter = FakeAdapter(
            positions=[{"symbol": "BTC-USDT", "side": "long",
                        "contracts": 100.0}],
            order={"id": "c", "filled": 100.0, "average": 50000.0},
        )

        self.assertTrue(broker(adapter, cap=1.0).close_position("BTC-USDT").ok)

    def test_a_close_that_does_not_fill_is_reported_as_not_ok(self):
        adapter = FakeAdapter(positions=[dict(LONG)],
                              order={"id": "c", "filled": 0})

        self.assertFalse(broker(adapter).close_position("BTC-USDT").ok)


class TestReducing(unittest.TestCase):
    """第十八節緊急保護的第 3 步。"""

    def test_it_reduces_by_the_given_fraction(self):
        adapter = FakeAdapter(positions=[dict(LONG)],
                              order={"id": "r", "filled": 0.005})

        self.assertTrue(broker(adapter).reduce_position("BTC-USDT", 0.5))
        self.assertAlmostEqual(
            adapter.kwargs_of("create_order")[0]["quantity"], 0.005,
        )

    def test_a_fraction_outside_zero_to_one_is_refused(self):
        for fraction in (0, 1, 1.5, -0.5, None, "half"):
            with self.subTest(fraction=fraction):
                adapter = FakeAdapter(positions=[dict(LONG)])
                self.assertFalse(
                    broker(adapter).reduce_position("BTC-USDT", fraction)
                )
                self.assertNotIn("create_order", adapter.names())


class TestTheClientIdLookup(unittest.TestCase):
    """
    第五節:不要靠模型記憶猜 API。

    對帳給的是 clientOrderId,而 ccxt 的 fetch_order 第一個參數是交易所
    的訂單編號。看 ccxt 4.5.78 的 bingx 實作:cancel_order 有 client-id
    分支(帶了就不送 orderId),**fetch_order 沒有** —— 它一律送
    orderId,再把 params merge 進去。

    所以直接查會多送一個假的 orderId,交易所很可能回「查無此單」,
    然後對帳把成交的單標成 REJECTED。改成列舉再比對。
    """

    def test_it_never_calls_fetch_order_with_a_client_id(self):
        adapter = FakeAdapter(open_orders=[
            {"id": "ex-1", "clientOrderId": "cid-1", "status": "open"},
        ])

        broker(adapter).fetch_order("cid-1", "BTC-USDT")

        self.assertNotIn("get_order", adapter.names())

    def test_an_open_order_is_found_by_its_client_id(self):
        adapter = FakeAdapter(open_orders=[
            {"id": "ex-9", "clientOrderId": "other", "status": "open"},
            {"id": "ex-1", "clientOrderId": "cid-1", "status": "open"},
        ])

        found = broker(adapter).fetch_order("cid-1", "BTC-USDT")

        self.assertEqual(found["state"], "submitted")
        self.assertEqual(found["exchange_order_id"], "ex-1")

    def test_a_finished_order_is_found_in_the_history(self):
        adapter = FakeAdapter(history=[
            {"id": "ex-2", "clientOrderId": "cid-1", "status": "closed",
             "filled": 0.01, "average": 50000.0},
        ])

        found = broker(adapter).fetch_order("cid-1", "BTC-USDT")

        self.assertEqual(found["state"], "filled")
        self.assertEqual(found["filled_quantity"], 0.01)

    def test_the_open_list_is_checked_before_the_history(self):
        """未結清單沒有時間窗,先看它比較不會誤判。"""
        adapter = FakeAdapter(open_orders=[
            {"id": "ex-1", "clientOrderId": "cid-1", "status": "open"},
        ])

        broker(adapter).fetch_order("cid-1", "BTC-USDT")

        self.assertNotIn("get_order_history", adapter.names())

    def test_the_history_query_carries_a_lookback_window(self):
        from agmcis.execution import live_broker as lb

        adapter = FakeAdapter()
        before = int(time.time() * 1000)

        broker(adapter).fetch_order("cid-1", "BTC-USDT")

        since = adapter.kwargs_of("get_order_history")[0]["since"]
        self.assertIsNotNone(since)
        self.assertLessEqual(since, before - lb.HISTORY_LOOKBACK_MS + 5000)

    def test_the_exchange_field_name_is_read_when_ccxt_did_not_unify_it(self):
        """
        BingX 永續回的是 clientOrderID(大寫 ID)。ccxt 的 parse_order
        會收斂成 clientOrderId,但少讀一個欄位會讓整個比對失效,
        所以 info 那一層也讀。
        """
        for key in ("clientOrderID", "clientOrderId", "origClientOrderId", "c"):
            with self.subTest(key=key):
                adapter = FakeAdapter(open_orders=[
                    {"id": "ex-1", "status": "open", "info": {key: "cid-1"}},
                ])

                found = broker(adapter).fetch_order("cid-1", "BTC-USDT")

                self.assertIsNotNone(found, key)

    def test_absent_from_both_lists_means_no_such_order(self):
        adapter = FakeAdapter(
            open_orders=[{"id": "a", "clientOrderId": "other"}],
            history=[{"id": "b", "clientOrderId": "another"}],
        )

        self.assertIsNone(broker(adapter).fetch_order("cid-1", "BTC-USDT"))

    def test_a_failed_open_query_raises_instead_of_answering(self):
        """
        查不到與查詢失敗混在一起,正是這個方法要避免的事。
        """
        adapter = FakeAdapter(fail={"get_open_orders"})

        with self.assertRaises(RuntimeError):
            broker(adapter).fetch_order("cid-1", "BTC-USDT")

    def test_a_failed_history_query_raises_instead_of_answering(self):
        adapter = FakeAdapter(fail={"get_order_history"})

        with self.assertRaises(RuntimeError):
            broker(adapter).fetch_order("cid-1", "BTC-USDT")

    def test_reconciliation_treats_a_failed_lookup_as_unknown_not_rejected(self):
        """
        查詢失敗的落點必須是「狀態不明,這張單不可以重送」,
        不是「交易所查無此單」。方向反了就是把成交當成沒送出去。
        """
        from agmcis.core.enums import MarketType, OrderSide, OrderState, OrderType
        from agmcis.core.models import Order
        from agmcis.execution import reconciliation as recon

        order = Order(
            client_order_id="cid-1", symbol="BTC-USDT",
            market_type=MarketType.PERPETUAL, side=OrderSide.BUY,
            order_type=OrderType.MARKET, quantity=1.0,
            state=OrderState.UNKNOWN,
        )

        class Store:
            def __init__(self):
                self.saved = []

            def unresolved(self):
                return [order]

            def save(self, *a, **kw):
                self.saved.append(a)

            def record_event(self, *a, **kw):
                pass

            def mark_reconciled(self, *a, **kw):
                pass

        store = Store()
        live = broker(FakeAdapter(fail={"get_open_orders"}))

        report = recon.Reconciler(
            store=store,
            fetch_order=live.fetch_order,
            fetch_positions=lambda: [],
            local_positions=lambda: [],
        ).run()

        self.assertIn(recon.ORDER_STILL_UNKNOWN,
                      [d.kind for d in report.discrepancies])
        self.assertEqual(store.saved, [], "狀態不明的單不該被改狀態")


class TestQueries(unittest.TestCase):

    def test_an_unknown_status_stays_unknown(self):
        """猜一個比較樂觀的值,會讓對帳把一張還活著的單當成結案。"""
        adapter = FakeAdapter(open_orders=[
            {"id": "x", "clientOrderId": "cid-1", "status": "沒看過的狀態"},
        ])

        state = broker(adapter).fetch_order("cid-1", "BTC-USDT")["state"]

        self.assertEqual(state, "unknown")

    def test_known_statuses_are_translated(self):
        for raw, expected in (
            ("open", "submitted"), ("closed", "filled"),
            ("canceled", "cancelled"), ("rejected", "rejected"),
        ):
            with self.subTest(raw=raw):
                adapter = FakeAdapter(open_orders=[
                    {"id": "x", "clientOrderId": "cid-1", "status": raw},
                ])
                self.assertEqual(
                    broker(adapter).fetch_order("cid-1", "BTC-USDT")["state"],
                    expected,
                )

    def test_positions_with_no_contracts_are_not_positions(self):
        adapter = FakeAdapter(positions=[
            {"symbol": "BTC-USDT", "side": "long", "contracts": 0},
            {"symbol": "ETH-USDT", "side": "short", "contracts": 1.0},
        ])

        held = broker(adapter).fetch_positions()

        self.assertEqual([p["symbol"] for p in held], ["ETH-USDT"])

    def test_get_position_returns_none_when_flat(self):
        adapter = FakeAdapter(positions=[
            {"symbol": "BTC-USDT", "side": "long", "contracts": 0},
        ])

        self.assertIsNone(broker(adapter).get_position("BTC-USDT"))

    def test_a_position_query_failure_propagates(self):
        """
        變成 None 的話,緊急流程會認為「已經沒有部位了」而停止動作 ——
        那正好是最需要動作的時候。
        """
        adapter = FakeAdapter(fail={"get_positions"})

        with self.assertRaises(RuntimeError):
            broker(adapter).get_position("BTC-USDT")


class TestCancelRemainder(unittest.TestCase):

    class Order:
        def __init__(self, order_id="ex-1", symbol="BTC-USDT"):
            self.exchange_order_id = order_id
            self.symbol = symbol

    def test_it_cancels_by_exchange_order_id(self):
        adapter = FakeAdapter()

        self.assertTrue(broker(adapter).cancel_remainder(self.Order()))
        self.assertEqual(adapter.kwargs_of("cancel_order")[0]["order_id"], "ex-1")

    def test_without_an_exchange_order_id_it_returns_false(self):
        """
        不假裝撤掉了。一張還活著的掛單會在稍後成交,
        而那時候沒有人在管它。
        """
        adapter = FakeAdapter()

        self.assertFalse(
            broker(adapter).cancel_remainder(self.Order(order_id=None))
        )

    def test_a_failed_cancel_returns_false(self):
        adapter = FakeAdapter(fail={"cancel_order"})

        self.assertFalse(broker(adapter).cancel_remainder(self.Order()))


class TestItNeverSwallowsFailures(unittest.TestCase):
    """第九十四節:不得靜默失敗。"""

    def test_there_is_no_bare_except_pass(self):
        import ast
        import inspect

        from agmcis.execution import live_broker

        tree = ast.parse(inspect.getsource(live_broker))

        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            body = [s for s in node.body if not isinstance(s, ast.Expr)
                    or not isinstance(s.value, ast.Constant)]
            self.assertFalse(
                len(body) == 1 and isinstance(body[0], ast.Pass),
                f"第 {node.lineno} 行有 except: pass",
            )


if __name__ == "__main__":
    unittest.main()


class TestTheEmergencyProtocolAgainstARealBroker(unittest.TestCase):
    """
    第十八節的六步驟緊急保護,由一個**真的實作了縮倉**的 broker 走一遍。

    這條路徑模擬盤走不到:PaperBroker 的 reduce_position 一律拋
    NotImplementedError(它沒辦法定義「縮掉的那一半用什麼價格結算」),
    所以緊急流程在模擬盤永遠是「重試 → 失敗 → 直接平倉」,
    中間那一步從來沒有被執行過。

    這裡不是驗「真的下單失敗時會怎樣」—— 那要真錢。
    這裡驗的是這六步接得起來,而且每一步的方向都對。
    """

    def protect(self, adapter, **kwargs):
        from agmcis.execution import emergency

        return emergency.protect(
            broker(adapter), "BTC-USDT", 49000.0,
            retries=2, pause_file=os.devnull,
            disable_orders=False, notifier=lambda *a, **k: None,
            **kwargs,
        )

    def test_an_already_protected_position_never_enters_the_protocol(self):
        adapter = FakeAdapter(
            positions=[dict(LONG)],
            open_orders=[{"id": "s", "type": "stop_market",
                          "reduceOnly": True}],
        )

        outcome = self.protect(adapter)

        self.assertEqual(outcome.status, "PROTECTED")
        self.assertNotIn("create_order", adapter.names())

    def test_the_reduce_step_actually_runs_on_a_broker_that_supports_it(self):
        """
        重試掛不上 → 縮倉 → 再掛一次 → 成功。中間那一步在模擬盤
        永遠走不到,因為 PaperBroker 的縮倉會拋 NotImplementedError。
        """
        from agmcis.execution import emergency

        stop = {"id": "s", "type": "stop_market", "reduceOnly": True}
        adapter = FakeAdapter(
            positions=[dict(LONG)],
            order={"id": "r", "filled": 0.005, "average": 50000.0},
        )

        # 掛了三次之後停損才真的出現在交易所上 ——
        # 前兩次重試看不到它,縮倉之後那一次看得到。
        placed = []
        original = adapter.create_order

        def counting_create_order(**kwargs):
            placed.append(kwargs)
            if len(placed) >= 4:
                adapter.open_orders = [stop]
            return original(**kwargs)

        adapter.create_order = counting_create_order

        outcome = emergency.protect(
            broker(adapter), "BTC-USDT", 49000.0,
            retries=2, pause_file=os.devnull,
            disable_orders=False, notifier=lambda *a, **k: None,
        )

        self.assertEqual(outcome.status, "PROTECTED_AFTER_REDUCE")
        self.assertEqual(outcome.reduced_fraction, emergency.DEFAULT_REDUCE_FRACTION)

        reduced = [k for k in placed if k["order_type"] is OrderType.MARKET]
        self.assertEqual(len(reduced), 1, "縮倉應該只送一張市價單")
        self.assertTrue(reduced[0]["reduce_only"])
        self.assertIs(reduced[0]["side"], OrderSide.SELL)

    def test_a_position_that_cannot_be_protected_gets_closed(self):
        """停損救不回來、縮倉也救不回來 → 平掉。不留裸倉。"""
        adapter = FakeAdapter(
            positions=[dict(LONG)],
            order={"id": "c", "filled": 0.01, "average": 50000.0},
        )

        outcome = self.protect(adapter)

        self.assertEqual(outcome.status, "CLOSED")

    def test_a_position_that_cannot_even_be_closed_is_STUCK_not_silent(self):
        """
        平不掉是最壞的情況,而最壞的情況必須喊出來 ——
        一個安靜失敗的緊急流程等於沒有緊急流程(第九十四節)。
        """
        adapter = FakeAdapter(
            positions=[dict(LONG)],
            order={"id": "c", "filled": 0},   # 一直沒成交
        )

        outcome = self.protect(adapter)

        self.assertEqual(outcome.status, "STUCK")
        self.assertTrue(outcome.reason)

    def test_a_broker_that_cannot_see_the_exchange_does_not_report_protected(self):
        """
        查不到掛單時 has_protection 回 False,所以緊急流程會啟動 ——
        而不是誤以為「有保護」然後什麼都不做。
        """
        adapter = FakeAdapter(
            positions=[dict(LONG)],
            order={"id": "c", "filled": 0.01},
            fail={"get_open_orders"},
        )

        outcome = self.protect(adapter)

        self.assertNotIn(outcome.status, ("PROTECTED", "PROTECTED_ON_RETRY"))
