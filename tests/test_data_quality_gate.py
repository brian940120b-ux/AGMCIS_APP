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
from agmcis.data import quality


class TestIndicatorFailuresAreVisible(unittest.TestCase):
    """
    Phase 2 起,K 棒品質在**算指標之前**就被檢查(get_ohlcv_checked),
    所以這裡 patch 的是那道 gate。行為要求不變:失敗必須被記錄,
    而且回傳 data_ok=False,絕不悄悄變成中性訊號。
    """

    def _report(self, code, detail):
        report = quality.QualityReport(symbol="BTC/USDT", timeframe="1h")
        report.add(code, quality.SEVERITY_ERROR, detail)
        return report

    def test_fetch_failure_marks_data_not_ok(self):
        report = self._report("FETCH_FAILED", "api down")

        with patch.object(technical_service, "get_price", return_value=100.0), \
             patch.object(technical_service, "get_ohlcv_checked", return_value=(None, report)), \
             patch.object(technical_service, "logger") as log:
            result = technical_service.get_indicators("BTC/USDT")

        self.assertFalse(result["data_ok"])
        self.assertIn("FETCH_FAILED", result["data_error"])
        self.assertTrue(log.warning.called, "失敗必須被記錄,不能靜默")

    def test_insufficient_candles_marks_data_not_ok(self):
        report = self._report("INSUFFICIENT_CANDLES", "只有 10 根,需要至少 60 根")

        with patch.object(technical_service, "get_price", return_value=100.0), \
             patch.object(technical_service, "get_ohlcv_checked", return_value=(None, report)), \
             patch.object(technical_service, "logger"):
            result = technical_service.get_indicators("BTC/USDT")

        self.assertFalse(result["data_ok"])
        self.assertIn("INSUFFICIENT_CANDLES", result["data_error"])

    def test_stale_data_marks_data_not_ok(self):
        report = self._report("STALE_DATA", "最後一根 K 棒已經是 240 分鐘前")

        with patch.object(technical_service, "get_price", return_value=100.0), \
             patch.object(technical_service, "get_ohlcv_checked", return_value=(None, report)), \
             patch.object(technical_service, "logger"):
            result = technical_service.get_indicators("BTC/USDT")

        self.assertFalse(result["data_ok"])
        self.assertIn("STALE_DATA", result["data_error"])
        self.assertTrue(result["data_issues"])

    def test_indicator_calculation_failure_is_logged(self):
        """K 棒過了品質檢查,但指標算出 NaN 時仍必須被擋下。"""
        import pandas as pd

        flat = pd.DataFrame({
            "timestamp": range(150), "open": [1.0] * 150, "high": [1.0] * 150,
            "low": [1.0] * 150, "close": [1.0] * 150, "volume": [1.0] * 150,
        })
        report = quality.QualityReport(symbol="BTC/USDT", timeframe="1h")

        with patch.object(technical_service, "get_price", return_value=1.0), \
             patch.object(technical_service, "get_ohlcv_checked", return_value=(flat, report)), \
             patch.object(technical_service, "RSIIndicator", side_effect=RuntimeError("boom")), \
             patch.object(technical_service, "logger") as log:
            result = technical_service.get_indicators("BTC/USDT")

        self.assertFalse(result["data_ok"])
        self.assertIn("indicator_calc_failed", result["data_error"])
        self.assertTrue(log.exception.called)


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
