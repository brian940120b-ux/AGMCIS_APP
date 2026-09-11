"""
ExchangeEngine 單元測試(BingX 單一合約交易所版本)

涵蓋情境:
  1. 成功呼叫 -> 直接回傳
  2. 網路錯誤 -> 依 EXCHANGE_MAX_RETRIES 重試,重試期間成功就回傳
  3. 重試用盡仍失敗 -> 拋出 ExchangeUnavailableError
  4. 不可重試的明確錯誤(如 symbol 不存在)-> 不浪費時間重試,直接失敗
  5. get_funding_rate / get_open_interest 失敗時回傳 None 而不是讓整體掛掉
  6. get_positions / get_balance / create_order 的方法簽名正確傳遞參數
     (這三個是下單/風控層會用到的合約專屬方法,先確保介面正確)

完全不連網路、不需要真實 API Key,靠假 ccxt 模組 + MagicMock 驗證邏輯正確性。
"""
import sys
import os
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ccxt

from exchange_engine import ExchangeEngine, ExchangeUnavailableError  # noqa: E402

# 重試次數與退避時間**明確傳進 engine**,不靠環境變數。
#
# 原本這裡寫 os.environ.setdefault(...) 再 import,但 settings 是在
# 模組載入時就把環境變數讀成常數的。整包測試一起跑時,別的測試模組
# 可能先載入了 settings,setdefault 就完全沒有作用 ——
# 這個測試會依 discovery 順序而時好時壞。
TEST_MAX_RETRIES = 2


def make_engine(mock_exchange):
    return ExchangeEngine(
        exchange_factory=lambda: mock_exchange,
        max_retries=TEST_MAX_RETRIES,
        backoff_seconds=0,            # 測試不用真的等待
    )


class TestExchangeEngine(unittest.TestCase):

    def test_get_ticker_success(self):
        mock = MagicMock()
        mock.fetch_ticker.return_value = {"last": 65000, "percentage": 1.2}

        engine = make_engine(mock)
        result = engine.get_ticker("BTC/USDT:USDT")

        self.assertEqual(result["price"], 65000)
        mock.fetch_ticker.assert_called_once_with("BTC/USDT:USDT")

    def test_retries_on_network_error_then_succeeds(self):
        mock = MagicMock()
        mock.fetch_ticker.side_effect = [
            ccxt.NetworkError("timeout"),
            {"last": 65010, "percentage": 1.1},
        ]

        engine = make_engine(mock)
        result = engine.get_ticker("BTC/USDT:USDT")

        self.assertEqual(result["price"], 65010)
        self.assertEqual(mock.fetch_ticker.call_count, 2)

    def test_raises_unavailable_after_retries_exhausted(self):
        mock = MagicMock()
        mock.fetch_ticker.side_effect = ccxt.NetworkError("down")

        engine = make_engine(mock)
        with self.assertRaises(ExchangeUnavailableError):
            engine.get_ticker("BTC/USDT:USDT")

        self.assertEqual(mock.fetch_ticker.call_count, TEST_MAX_RETRIES)

    def test_non_retryable_error_fails_immediately(self):
        mock = MagicMock()
        mock.fetch_ticker.side_effect = ccxt.BadSymbol("symbol not found")

        engine = make_engine(mock)
        with self.assertRaises(ExchangeUnavailableError):
            engine.get_ticker("FAKE/USDT:USDT")

        mock.fetch_ticker.assert_called_once()  # 不該重試

    def test_funding_rate_returns_none_on_failure(self):
        mock = MagicMock()
        mock.fetch_funding_rate.side_effect = ccxt.NetworkError("down")

        engine = make_engine(mock)
        result = engine.get_funding_rate("BTC/USDT:USDT")

        self.assertIsNone(result)

    def test_open_interest_returns_none_on_failure(self):
        mock = MagicMock()
        mock.fetch_open_interest.side_effect = ccxt.NetworkError("down")

        engine = make_engine(mock)
        result = engine.get_open_interest("BTC/USDT:USDT")

        self.assertIsNone(result)

    def test_ohlcv_shape_is_normalized(self):
        mock = MagicMock()
        mock.fetch_ohlcv.return_value = [
            [1719900000000, 65000, 65100, 64900, 65050, 12.5],
        ]

        engine = make_engine(mock)
        candles = engine.get_ohlcv("BTC/USDT:USDT", "15m", 1)

        self.assertEqual(len(candles), 1)
        self.assertEqual(
            set(candles[0].keys()),
            {"time", "open", "high", "low", "close", "volume"},
        )
        self.assertEqual(candles[0]["close"], 65050)

    def test_get_positions_passes_symbols(self):
        mock = MagicMock()
        mock.fetch_positions.return_value = [{"symbol": "BTC/USDT:USDT", "contracts": 1}]

        engine = make_engine(mock)
        result = engine.get_positions(["BTC/USDT:USDT"])

        self.assertEqual(len(result), 1)
        mock.fetch_positions.assert_called_once_with(["BTC/USDT:USDT"])

    def test_get_balance(self):
        mock = MagicMock()
        mock.fetch_balance.return_value = {"USDT": {"free": 1000}}

        engine = make_engine(mock)
        result = engine.get_balance()

        self.assertEqual(result["USDT"]["free"], 1000)

    def test_create_order_passes_correct_args(self):
        mock = MagicMock()
        mock.create_order.return_value = {"id": "12345", "status": "open"}

        engine = make_engine(mock)
        result = engine.create_order("BTC/USDT:USDT", "buy", 0.01, "market")

        self.assertEqual(result["id"], "12345")
        mock.create_order.assert_called_once_with(
            "BTC/USDT:USDT", "market", "buy", 0.01, None, {}
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
