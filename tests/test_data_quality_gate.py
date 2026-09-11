"""
資料品質 gate。

原本 technical_service 是 `except Exception: pass`,指標算失敗回傳全 None 與
trend="UNKNOWN",而 calculate_scanner_confidence 遇到 None 會給出 50 分的「中性」分數 ——
系統無法區分「市場中性」與「資料壞掉」,可能在壞資料上開倉。
"""
import unittest
from unittest.mock import patch

import decision_engine
import direction_engine
import scanner_service
import technical_service


class TestIndicatorFailuresAreVisible(unittest.TestCase):

    def test_ohlcv_failure_marks_data_not_ok(self):
        with patch.object(technical_service, "get_price", return_value=100.0), \
             patch.object(technical_service, "get_ohlcv", side_effect=RuntimeError("api down")), \
             patch.object(technical_service, "logger") as log:
            result = technical_service.get_indicators("BTC/USDT")

        self.assertFalse(result["data_ok"])
        self.assertIn("ohlcv_fetch_failed", result["data_error"])
        self.assertTrue(log.error.called, "失敗必須被記錄,不能靜默")

    def test_none_ohlcv_marks_data_not_ok(self):
        with patch.object(technical_service, "get_price", return_value=100.0), \
             patch.object(technical_service, "get_ohlcv", return_value=None), \
             patch.object(technical_service, "logger"):
            result = technical_service.get_indicators("BTC/USDT")

        self.assertFalse(result["data_ok"])
        self.assertIn("insufficient_candles", result["data_error"])

    def test_insufficient_candles_marks_data_not_ok(self):
        import pandas as pd
        short_df = pd.DataFrame(
            {"open": [1] * 10, "high": [1] * 10, "low": [1] * 10,
             "close": [1] * 10, "volume": [1] * 10}
        )
        with patch.object(technical_service, "get_price", return_value=100.0), \
             patch.object(technical_service, "get_ohlcv", return_value=short_df), \
             patch.object(technical_service, "logger") as log:
            result = technical_service.get_indicators("BTC/USDT")

        self.assertFalse(result["data_ok"])
        self.assertTrue(log.warning.called)


class TestBadDataNeverBecomesASignal(unittest.TestCase):

    BAD = {"data_ok": False, "data_error": "ohlcv_fetch_failed: boom",
           "trend": "UNKNOWN", "rsi": None, "macd": None, "macd_signal": None,
           "ema20": None, "ema60": None, "atr": None, "price": 100.0}

    def test_confidence_is_none_not_fifty(self):
        self.assertIsNone(scanner_service.calculate_scanner_confidence(self.BAD))

    def test_direction_is_wait(self):
        self.assertEqual(direction_engine.get_trade_direction(self.BAD), "WAIT")

    def test_trade_signal_is_no_data(self):
        self.assertEqual(
            decision_engine.get_trade_signal(None, "WAIT", self.BAD), "⚪ No Data"
        )

    def test_scan_market_emits_no_data_row_and_skips_scoring(self):
        with patch.object(scanner_service, "SCAN_SYMBOLS", ["BTC/USDT"]), \
             patch.object(scanner_service, "get_indicators", return_value=dict(self.BAD)), \
             patch.object(scanner_service, "analyze_timeframes") as mtf:
            rows = scanner_service.scan_market()

        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["data_ok"])
        self.assertEqual(rows[0]["trade_signal"], "⚪ No Data")
        self.assertIsNone(rows[0]["stoploss"])
        self.assertIn("資料異常", rows[0]["blocked_reason"])
        mtf.assert_not_called()


class TestStopLossSideMatchesDirection(unittest.TestCase):

    GOOD = {"data_ok": True, "trend": "BEARISH", "rsi": 30, "macd": -1,
            "macd_signal": 0, "ema20": 90, "ema60": 100, "atr": 2.0, "price": 100.0,
            "macd_hist": -0.5}

    def test_short_stop_loss_is_above_entry(self):
        """做空的停損必須在進場價上方。原本一律用 price - atr*2,做空會立刻停損。"""
        with patch.object(scanner_service, "SCAN_SYMBOLS", ["BTC/USDT"]), \
             patch.object(scanner_service, "get_indicators", return_value=dict(self.GOOD)), \
             patch.object(scanner_service, "analyze_timeframes", return_value={}), \
             patch.object(scanner_service, "calculate_mtf_score",
                          return_value={"mtf_score": -3, "mtf_status": "BEARISH",
                                        "blocked_reason": None}), \
             patch.object(scanner_service, "get_trade_direction", return_value="SHORT"), \
             patch.object(scanner_service, "get_trade_signal", return_value="🔴 Sell"):
            rows = scanner_service.scan_market()

        row = rows[0]
        self.assertEqual(row["action"], "SHORT")
        self.assertGreater(row["stoploss"], row["entry_price"])
        self.assertLess(row["takeprofit"], row["entry_price"])

    def test_long_stop_loss_is_below_entry(self):
        with patch.object(scanner_service, "SCAN_SYMBOLS", ["BTC/USDT"]), \
             patch.object(scanner_service, "get_indicators", return_value=dict(self.GOOD)), \
             patch.object(scanner_service, "analyze_timeframes", return_value={}), \
             patch.object(scanner_service, "calculate_mtf_score",
                          return_value={"mtf_score": 3, "mtf_status": "STRONG_BULLISH",
                                        "blocked_reason": None}), \
             patch.object(scanner_service, "get_trade_direction", return_value="LONG"), \
             patch.object(scanner_service, "get_trade_signal", return_value="🟢 Strong Buy"):
            rows = scanner_service.scan_market()

        row = rows[0]
        self.assertLess(row["stoploss"], row["entry_price"])
        self.assertGreater(row["takeprofit"], row["entry_price"])
