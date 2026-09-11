"""
ExchangeAdapter(BingX)。

重點:
  1. 核心行情失敗要拋 ExchangeUnavailableError —— 呼叫端必須知道自己沒有資料,
     不能拿舊值當新值默默用下去。
  2. 輔助資料失敗回 None —— 這些缺了不該讓整個 Dashboard 掛掉。
  3. Standard 與 Perpetual 的符號格式必須分開。
  4. 交易所實體是注入的,不在 import 時建立。
"""
import unittest
from unittest.mock import MagicMock

import ccxt

from agmcis.core.enums import MarketType, OrderSide, OrderType
from agmcis.core.errors import ExchangeUnavailableError
from agmcis.exchange.base import ExchangeAdapter
from agmcis.exchange.bingx.adapter import BingXAdapter


def adapter(mock, **kwargs):
    kwargs.setdefault("max_retries", 2)
    kwargs.setdefault("backoff_seconds", 0)
    return BingXAdapter(exchange_factory=lambda market_type=None: mock, **kwargs)


class TestSymbolConversion(unittest.TestCase):

    def setUp(self):
        self.a = adapter(MagicMock())

    def test_perpetual_gets_settlement_suffix(self):
        self.assertEqual(
            self.a.to_market_symbol("BTC/USDT", MarketType.PERPETUAL), "BTC/USDT:USDT"
        )

    def test_standard_is_rejected_loudly(self):
        """
        Phase 3 實測發現 ccxt 的 bingx 沒有 futures 市場型態
        (has['future'] 是 False),Standard Contract 只有三個 private 端點。

        原本這裡會回傳一個看起來合理的符號,然後在更深的地方失敗,
        錯誤訊息完全看不出根因。現在早點失敗、訊息說清楚。
        """
        with self.assertRaises(ExchangeUnavailableError) as ctx:
            self.a.to_market_symbol("BTC/USDT", MarketType.STANDARD)
        self.assertIn("Standard Futures", str(ctx.exception))

    def test_already_converted_symbol_is_left_alone(self):
        self.assertEqual(
            self.a.to_market_symbol("BTC/USDT:USDT", MarketType.PERPETUAL), "BTC/USDT:USDT"
        )

    def test_lowercase_is_normalized(self):
        self.assertEqual(
            self.a.to_market_symbol("btc/usdt", MarketType.PERPETUAL), "BTC/USDT:USDT"
        )


class TestCoreMarketDataRaises(unittest.TestCase):
    """核心行情失敗必須讓呼叫端知道。"""

    def test_ticker_success_is_normalized(self):
        mock = MagicMock()
        mock.fetch_ticker.return_value = {
            "last": 65000, "bid": 64999, "ask": 65001,
            "high": 66000, "low": 64000, "percentage": 1.2,
            "baseVolume": 1000, "quoteVolume": 65_000_000, "timestamp": 123,
        }
        result = adapter(mock).get_ticker("BTC/USDT")

        self.assertEqual(result["price"], 65000)
        self.assertEqual(result["symbol"], "BTC/USDT")
        self.assertEqual(result["market_symbol"], "BTC/USDT:USDT")
        mock.fetch_ticker.assert_called_once_with("BTC/USDT:USDT")

    def test_retries_then_succeeds(self):
        mock = MagicMock()
        mock.fetch_ticker.side_effect = [
            ccxt.NetworkError("timeout"), {"last": 65010},
        ]
        self.assertEqual(adapter(mock).get_ticker("BTC/USDT")["price"], 65010)
        self.assertEqual(mock.fetch_ticker.call_count, 2)

    def test_raises_after_retries_exhausted(self):
        mock = MagicMock()
        mock.fetch_ticker.side_effect = ccxt.NetworkError("down")

        with self.assertRaises(ExchangeUnavailableError):
            adapter(mock).get_ticker("BTC/USDT")
        self.assertEqual(mock.fetch_ticker.call_count, 2)

    def test_non_retryable_error_fails_immediately(self):
        """交易所明確拒絕(symbol 不存在)重試沒用,不要浪費時間。"""
        mock = MagicMock()
        mock.fetch_ticker.side_effect = ccxt.BadSymbol("symbol not found")

        with self.assertRaises(ExchangeUnavailableError):
            adapter(mock).get_ticker("FAKE/USDT")
        mock.fetch_ticker.assert_called_once()

    def test_ohlcv_returns_raw_rows(self):
        mock = MagicMock()
        mock.fetch_ohlcv.return_value = [[1, 2, 3, 4, 5, 6]]
        rows = adapter(mock).get_ohlcv("BTC/USDT", "15m", 1)

        self.assertEqual(rows, [[1, 2, 3, 4, 5, 6]])
        mock.fetch_ohlcv.assert_called_once_with("BTC/USDT:USDT", "15m", None, 1)

    def test_ohlcv_raises_on_failure(self):
        mock = MagicMock()
        mock.fetch_ohlcv.side_effect = ccxt.NetworkError("down")
        with self.assertRaises(ExchangeUnavailableError):
            adapter(mock).get_ohlcv("BTC/USDT")


class TestAuxiliaryDataReturnsNone(unittest.TestCase):
    """輔助資料失敗不該讓整個系統掛掉。"""

    def test_funding_rate_returns_none_on_failure(self):
        mock = MagicMock()
        mock.fetch_funding_rate.side_effect = ccxt.NetworkError("down")
        self.assertIsNone(adapter(mock).get_funding_rate("BTC/USDT"))

    def test_open_interest_returns_none_on_failure(self):
        mock = MagicMock()
        mock.fetch_open_interest.side_effect = ccxt.NetworkError("down")
        self.assertIsNone(adapter(mock).get_open_interest("BTC/USDT"))

    def test_order_book_returns_none_on_failure(self):
        mock = MagicMock()
        mock.fetch_order_book.side_effect = ccxt.NetworkError("down")
        self.assertIsNone(adapter(mock).get_order_book("BTC/USDT"))

    def test_standard_futures_has_no_funding_rate(self):
        """Standard Futures 沒有資金費率,不該白打一次 API。"""
        mock = MagicMock()
        result = adapter(mock).get_funding_rate("BTC/USDT", MarketType.STANDARD)

        self.assertIsNone(result)
        mock.fetch_funding_rate.assert_not_called()

    def test_funding_rate_is_normalized(self):
        mock = MagicMock()
        mock.fetch_funding_rate.return_value = {
            "fundingRate": 0.0001, "fundingTimestamp": 999, "markPrice": 65000,
        }
        result = adapter(mock).get_funding_rate("BTC/USDT")

        self.assertEqual(result["funding_rate"], 0.0001)
        self.assertEqual(result["next_funding_time"], 999)


class TestOrderBookMetrics(unittest.TestCase):

    def _book(self, bids, asks):
        mock = MagicMock()
        mock.fetch_order_book.return_value = {"bids": bids, "asks": asks, "timestamp": 1}
        return adapter(mock).get_order_book("BTC/USDT")

    def test_computes_spread_and_imbalance(self):
        book = self._book([[100.0, 3.0]], [[101.0, 1.0]])

        self.assertEqual(book["best_bid"], 100.0)
        self.assertEqual(book["best_ask"], 101.0)
        self.assertAlmostEqual(book["spread"], 1.0)
        self.assertAlmostEqual(book["spread_pct"], 1.0 / 100.5 * 100)
        # 買盤 3、賣盤 1 -> 正的失衡代表買盤較厚
        self.assertAlmostEqual(book["imbalance"], 0.5)

    def test_balanced_book_has_zero_imbalance(self):
        book = self._book([[100.0, 2.0]], [[101.0, 2.0]])
        self.assertAlmostEqual(book["imbalance"], 0.0)

    def test_empty_side_returns_none(self):
        self.assertIsNone(self._book([], [[101.0, 1.0]]))


class TestTradingRules(unittest.TestCase):

    def _adapter_with_market(self, market):
        mock = MagicMock()
        mock.load_markets.return_value = {"BTC/USDT:USDT": market}
        return adapter(mock)

    def test_rules_come_from_the_exchange(self):
        """交易規則一律動態取得,不可寫死。"""
        a = self._adapter_with_market({
            "active": True, "type": "swap", "quote": "USDT", "contractSize": 1,
            "precision": {"price": 0.1, "amount": 0.001},
            "limits": {
                "amount": {"min": 0.001, "max": 1000},
                "cost": {"min": 5},
                "leverage": {"max": 125},
            },
        })
        rules = a.get_trading_rules("BTC/USDT", MarketType.PERPETUAL)

        self.assertEqual(rules.exchange, "bingx")
        self.assertIs(rules.market_type, MarketType.PERPETUAL)
        self.assertEqual(rules.min_qty, 0.001)
        self.assertEqual(rules.min_notional, 5)
        self.assertEqual(rules.max_leverage, 125)
        self.assertEqual(rules.price_precision, 1)
        self.assertEqual(rules.qty_precision, 3)

    def test_missing_contract_raises(self):
        a = self._adapter_with_market({})
        mock_markets = {}
        a._markets_cache[MarketType.PERPETUAL] = mock_markets

        with self.assertRaises(ExchangeUnavailableError):
            a.get_trading_rules("NOPE/USDT", MarketType.PERPETUAL)

    def test_rules_key_separates_market_types(self):
        """(exchange, market_type, symbol) 才是唯一鍵。"""
        from agmcis.core.models import TradingRules

        perp = TradingRules(exchange="bingx", market_type=MarketType.PERPETUAL,
                            symbol="BTC/USDT")
        standard = TradingRules(exchange="bingx", market_type=MarketType.STANDARD,
                                symbol="BTC/USDT")
        self.assertNotEqual(perp.key, standard.key)


class TestMarketListing(unittest.TestCase):

    def test_filters_to_usdt_and_active_contracts(self):
        mock = MagicMock()
        mock.load_markets.return_value = {
            "BTC/USDT:USDT": {"active": True, "type": "swap", "quote": "USDT", "base": "BTC"},
            "ETH/BTC:BTC": {"active": True, "type": "swap", "quote": "BTC", "base": "ETH"},
            "OLD/USDT:USDT": {"active": False, "type": "swap", "quote": "USDT", "base": "OLD"},
            "SPOT/USDT": {"active": True, "type": "spot", "quote": "USDT", "base": "SPOT"},
        }
        markets = adapter(mock).list_markets(MarketType.PERPETUAL)

        self.assertEqual([m["symbol"] for m in markets], ["BTC/USDT:USDT"])

    def test_markets_are_cached(self):
        mock = MagicMock()
        mock.load_markets.return_value = {}
        a = adapter(mock)

        a.list_markets(MarketType.PERPETUAL)
        a.list_markets(MarketType.PERPETUAL)
        mock.load_markets.assert_called_once()


class TestAccountAndOrders(unittest.TestCase):

    def test_get_balance(self):
        mock = MagicMock()
        mock.fetch_balance.return_value = {"USDT": {"free": 1000}}
        self.assertEqual(adapter(mock).get_balance()["USDT"]["free"], 1000)

    def test_get_positions_passes_symbols(self):
        mock = MagicMock()
        mock.fetch_positions.return_value = [{"symbol": "BTC/USDT:USDT"}]
        result = adapter(mock).get_positions(["BTC/USDT:USDT"])

        self.assertEqual(len(result), 1)
        mock.fetch_positions.assert_called_once_with(["BTC/USDT:USDT"])

    def test_create_order_passes_client_order_id_and_reduce_only(self):
        mock = MagicMock()
        mock.create_order.return_value = {"id": "1"}

        adapter(mock).create_order(
            "BTC/USDT", OrderSide.BUY, 0.01, OrderType.MARKET,
            client_order_id="AGMCIS-1", reduce_only=True,
        )

        args = mock.create_order.call_args.args
        self.assertEqual(args[0], "BTC/USDT:USDT")
        self.assertEqual(args[1], "market")
        self.assertEqual(args[2], "buy")
        self.assertEqual(args[5], {"clientOrderId": "AGMCIS-1", "reduceOnly": True})

    def test_get_order_exists_for_reconciliation(self):
        """OrderState.UNKNOWN 時的正確反應是查詢,不是重送。"""
        mock = MagicMock()
        mock.fetch_order.return_value = {"id": "1", "status": "closed"}
        result = adapter(mock).get_order("1", "BTC/USDT")

        self.assertEqual(result["status"], "closed")
        mock.fetch_order.assert_called_once_with("1", "BTC/USDT:USDT")


class TestAdapterContract(unittest.TestCase):

    def test_bingx_implements_the_interface(self):
        self.assertTrue(issubclass(BingXAdapter, ExchangeAdapter))

    def test_exchange_is_injected_not_built_at_import(self):
        """能注入才能測試,也才能切到 paper 或 testnet。"""
        factory = MagicMock(return_value=MagicMock())
        a = BingXAdapter(exchange_factory=factory)

        factory.assert_not_called()      # 建構時不該連交易所
        a.get_ticker("BTC/USDT")
        factory.assert_called()

    def test_capabilities_are_declared(self):
        a = adapter(MagicMock())
        self.assertTrue(a.supports("funding_rate"))
        self.assertTrue(a.supports("order_book"))
        # Standard Futures 尚未對真實市場驗證過,不該宣稱支援
        self.assertFalse(a.supports("standard_futures"))
        self.assertFalse(a.supports("websocket"))

    def test_ping_reports_failure_without_raising(self):
        mock = MagicMock()
        mock.fetch_ticker.side_effect = ccxt.NetworkError("down")
        result = adapter(mock).ping()

        self.assertFalse(result["success"])
        self.assertIn("error", result)


class TestUniverseUsesPublicApi(unittest.TestCase):
    """
    exchange_universe 必須走 adapter 的公開介面,不能伸手進 _call。
    抽象層的價值就在於「呼叫端不知道底層是 ccxt」。
    """

    def test_get_tickers_is_part_of_the_interface(self):
        self.assertTrue(hasattr(ExchangeAdapter, "get_tickers"))

    def test_get_tickers_returns_all_contracts(self):
        mock = MagicMock()
        mock.fetch_tickers.return_value = {"BTC/USDT:USDT": {"quoteVolume": 1}}
        self.assertEqual(len(adapter(mock).get_tickers()), 1)

    def test_get_tickers_raises_on_failure(self):
        mock = MagicMock()
        mock.fetch_tickers.side_effect = ccxt.NetworkError("down")
        with self.assertRaises(ExchangeUnavailableError):
            adapter(mock).get_tickers()

    def test_universe_returns_empty_when_exchange_is_down(self):
        """拿不到資料時排名應該是空的 —— 沒有候選就不會開倉,這是正確反應。"""
        import exchange_universe
        from agmcis.data import market_data

        mock = MagicMock()
        mock.fetch_tickers.side_effect = ccxt.NetworkError("down")
        market_data.set_adapter(adapter(mock))
        try:
            self.assertEqual(exchange_universe.get_top_volume_symbols(), [])
        finally:
            market_data.set_adapter(None)

    def test_universe_only_returns_bingx_tradable_usdt_contracts(self):
        import exchange_universe
        from agmcis.data import market_data

        mock = MagicMock()
        mock.fetch_tickers.return_value = {
            "BTC/USDT:USDT": {"quoteVolume": 900},
            "ETH/USDT:USDT": {"quoteVolume": 500},
            "ETH/BTC:BTC": {"quoteVolume": 999},      # 非 USDT
            "BTC3L/USDT:USDT": {"quoteVolume": 999},  # 槓桿代幣
            "DEAD/USDT:USDT": {"quoteVolume": 0},     # 沒有成交量
        }
        market_data.set_adapter(adapter(mock))
        try:
            result = exchange_universe.get_top_volume_symbols(limit=10)
        finally:
            market_data.set_adapter(None)

        self.assertEqual([r["symbol"] for r in result], ["BTC/USDT", "ETH/USDT"])
        self.assertTrue(all(r["exchanges"] == ["bingx"] for r in result))


class TestRetryPolicyIsApplied(unittest.TestCase):
    """
    _call 的重試行為現在由 error_policy 決定。
    這裡確認策略真的被套用,而不是所有錯誤都拿到一樣的待遇。
    """

    def _adapter(self, mock, limiter=None):
        self.slept = []
        return BingXAdapter(
            exchange_factory=lambda mt=None: mock,
            max_retries=3, backoff_seconds=1.0,
            rate_limiter=limiter, sleep=self.slept.append,
        )

    def test_rate_limit_triggers_cooldown_not_a_normal_retry(self):
        from agmcis.exchange.rate_limiter import RateLimiter

        limiter = RateLimiter(max_calls=1000, period_seconds=10.0)
        mock = MagicMock()
        mock.fetch_ticker.side_effect = [
            ccxt.RateLimitExceeded("429"), {"last": 65000},
        ]
        a = self._adapter(mock, limiter)
        a.get_ticker("BTC/USDT")

        # 限流走冷卻,不是一般的 sleep 退避
        self.assertEqual(limiter.status()["cooldown_count"], 1)

    def test_clock_skew_resyncs_time_instead_of_blind_retry(self):
        mock = MagicMock()
        mock.fetch_ticker.side_effect = [
            ccxt.InvalidNonce("timestamp"), {"last": 65000},
        ]
        mock.fetch_time.return_value = 1_700_000_000_000

        a = self._adapter(mock)
        a.get_ticker("BTC/USDT")

        mock.fetch_time.assert_called_once()

    def test_clock_skew_gives_up_after_one_resync(self):
        """對過時還是失敗就不要一直對時。"""
        mock = MagicMock()
        mock.fetch_ticker.side_effect = ccxt.InvalidNonce("timestamp")
        mock.fetch_time.return_value = 1_700_000_000_000

        a = self._adapter(mock)
        with self.assertRaises(ExchangeUnavailableError):
            a.get_ticker("BTC/USDT")

        self.assertEqual(mock.fetch_time.call_count, 1)

    def test_auth_error_is_not_retried(self):
        mock = MagicMock()
        mock.fetch_balance.side_effect = ccxt.AuthenticationError("bad key")

        a = self._adapter(mock)
        with self.assertRaises(ExchangeUnavailableError):
            a.get_balance()

        mock.fetch_balance.assert_called_once()

    def test_write_timeout_demands_reconciliation_and_never_retries(self):
        """
        送出訂單後逾時:交易所可能已經收到。
        重送是重複開倉最常見的來源,所以這裡必須拋 OrderStateUnknownError。
        """
        from agmcis.core.errors import OrderStateUnknownError

        mock = MagicMock()
        mock.create_order.side_effect = ccxt.RequestTimeout("no response")

        a = self._adapter(mock)
        with self.assertRaises(OrderStateUnknownError):
            a.create_order("BTC/USDT", OrderSide.BUY, 0.01)

        mock.create_order.assert_called_once()

    def test_insufficient_funds_on_write_is_a_plain_rejection(self):
        """交易所明確拒絕代表它沒有收單,不需要對帳。"""
        from agmcis.core.errors import OrderRejectedError

        mock = MagicMock()
        mock.create_order.side_effect = ccxt.InsufficientFunds("no margin")

        a = self._adapter(mock)
        with self.assertRaises(OrderRejectedError):
            a.create_order("BTC/USDT", OrderSide.BUY, 0.01)


class TestServerTimeSync(unittest.TestCase):

    def _adapter(self, mock):
        return BingXAdapter(
            exchange_factory=lambda mt=None: mock,
            max_retries=1, backoff_seconds=0, sleep=lambda s: None,
        )

    def test_reports_offset(self):
        import time as _time

        mock = MagicMock()
        mock.fetch_time.return_value = _time.time() * 1000 + 500

        a = self._adapter(mock)
        offset = a.sync_server_time()

        self.assertIsNotNone(offset)
        self.assertTrue(a.clock_status()["within_tolerance"])

    def test_large_skew_is_flagged(self):
        """時鐘偏移的錯誤訊息看起來像 API Key 有問題,必須明確標出來。"""
        import time as _time

        mock = MagicMock()
        mock.fetch_time.return_value = _time.time() * 1000 + 60_000

        a = self._adapter(mock)
        a.sync_server_time()

        self.assertFalse(a.clock_status()["within_tolerance"])

    def test_failure_returns_none_without_raising(self):
        mock = MagicMock()
        mock.fetch_time.side_effect = ccxt.NetworkError("down")

        a = self._adapter(mock)
        self.assertIsNone(a.sync_server_time())
        self.assertFalse(a.clock_status()["synced"])


class TestStandardFuturesIsRejectedEverywhere(unittest.TestCase):
    """
    Phase 3 實測:ccxt 的 bingx has['future'] 是 False,
    而 defaultType='futures' 建構時不報錯、呼叫時才炸。
    所以每一條進入點都要明確擋下,不能留下「看起來能跑」的路徑。
    """

    def setUp(self):
        self.a = adapter(MagicMock())

    def test_symbol_conversion_rejects(self):
        with self.assertRaises(ExchangeUnavailableError):
            self.a.to_market_symbol("BTC/USDT", MarketType.STANDARD)

    def test_ticker_rejects(self):
        with self.assertRaises(ExchangeUnavailableError):
            self.a.get_ticker("BTC/USDT", MarketType.STANDARD)

    def test_ohlcv_rejects(self):
        with self.assertRaises(ExchangeUnavailableError):
            self.a.get_ohlcv("BTC/USDT", "1h", 10, MarketType.STANDARD)

    def test_trading_rules_reject(self):
        with self.assertRaises(ExchangeUnavailableError):
            self.a.get_trading_rules("BTC/USDT", MarketType.STANDARD)

    def test_capabilities_does_not_claim_support(self):
        self.assertFalse(self.a.supports("standard_futures"))

    def test_reason_explains_why(self):
        from agmcis.exchange.bingx.adapter import STANDARD_UNSUPPORTED_REASON
        self.assertIn("has['future']", STANDARD_UNSUPPORTED_REASON)
        self.assertIn("PHASE_3_REPORT", STANDARD_UNSUPPORTED_REASON)
