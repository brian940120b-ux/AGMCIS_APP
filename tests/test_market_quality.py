"""
市場資料品質檢查。

Phase 2 的完成判準:**資料異常時系統回 NO TRADE,而不是猜測。**
這裡逐項鎖住 Master Prompt 第 50 條要求的檢查。
"""
import unittest

from agmcis.data import quality

HOUR_MS = 3_600_000
BASE_TS = 1_700_000_000_000


def candles(count=80, start_price=100.0, interval=HOUR_MS, start_ts=BASE_TS, volume=10.0):
    """產生一段乾淨、時間連續、價格緩步上升的 K 棒。"""
    rows = []
    price = start_price
    for i in range(count):
        open_ = price
        close = price * 1.001
        rows.append([start_ts + i * interval, open_, max(open_, close) * 1.001,
                     min(open_, close) * 0.999, close, volume])
        price = close
    return rows


def now_after(rows, interval=HOUR_MS):
    """剛好在最後一根之後一個週期,讓 staleness 檢查通過。"""
    return rows[-1][0] + interval


class TestCleanDataPasses(unittest.TestCase):

    def test_clean_candles_have_no_errors(self):
        rows = candles()
        report = quality.check_ohlcv(rows, "BTC/USDT", "1h", now_ms=now_after(rows))

        self.assertTrue(report.ok, report.summary)
        self.assertEqual(report.errors, [])
        self.assertEqual(report.candle_count, 80)


class TestStructuralProblems(unittest.TestCase):

    def _check(self, rows, **kwargs):
        return quality.check_ohlcv(
            rows, "BTC/USDT", "1h", now_ms=kwargs.pop("now_ms", now_after(rows)), **kwargs
        )

    def test_empty_is_rejected(self):
        report = quality.check_ohlcv([], "BTC/USDT", "1h")
        self.assertFalse(report.ok)
        self.assertEqual(report.errors[0].code, "EMPTY")

    def test_too_few_candles_is_rejected(self):
        rows = candles(10)
        report = self._check(rows)
        self.assertFalse(report.ok)
        self.assertIn("INSUFFICIENT_CANDLES", [e.code for e in report.errors])

    def test_malformed_row_is_rejected(self):
        rows = candles()
        rows[5] = [BASE_TS, 1, 2]
        report = self._check(rows)
        self.assertFalse(report.ok)
        self.assertIn("MALFORMED_ROW", [e.code for e in report.errors])

    def test_non_numeric_is_rejected(self):
        rows = candles()
        rows[5][4] = "abc"
        report = self._check(rows)
        self.assertFalse(report.ok)
        self.assertIn("NON_NUMERIC", [e.code for e in report.errors])

    def test_nan_is_rejected(self):
        rows = candles()
        rows[5][4] = float("nan")
        report = self._check(rows)
        self.assertFalse(report.ok)
        self.assertIn("NAN_VALUE", [e.code for e in report.errors])

    def test_zero_price_is_rejected(self):
        rows = candles()
        rows[5][4] = 0
        report = self._check(rows)
        self.assertFalse(report.ok)
        self.assertIn("NON_POSITIVE_PRICE", [e.code for e in report.errors])

    def test_negative_volume_is_rejected(self):
        rows = candles()
        rows[5][5] = -1
        report = self._check(rows)
        self.assertFalse(report.ok)
        self.assertIn("NEGATIVE_VOLUME", [e.code for e in report.errors])


class TestTimestampProblems(unittest.TestCase):

    def test_duplicate_candles_are_rejected(self):
        rows = candles()
        rows[10][0] = rows[9][0]
        report = quality.check_ohlcv(rows, "BTC/USDT", "1h", now_ms=now_after(rows))
        self.assertFalse(report.ok)
        self.assertIn("DUPLICATE_CANDLE", [e.code for e in report.errors])

    def test_out_of_order_candles_are_rejected(self):
        rows = candles()
        rows[10][0], rows[11][0] = rows[11][0], rows[10][0]
        report = quality.check_ohlcv(rows, "BTC/USDT", "1h", now_ms=now_after(rows))
        self.assertFalse(report.ok)
        self.assertIn("OUT_OF_ORDER", [e.code for e in report.errors])

    def test_large_gap_is_an_error(self):
        """缺很多根 K 棒代表那段時間的資料不完整,指標會算錯。"""
        rows = candles(80)
        for i in range(40, 80):
            rows[i][0] += 20 * HOUR_MS      # 中間整整少了 20 根
        report = quality.check_ohlcv(rows, "BTC/USDT", "1h", now_ms=now_after(rows))
        self.assertFalse(report.ok)
        self.assertIn("MISSING_CANDLES", [e.code for e in report.errors])

    def test_small_gap_is_only_a_warning(self):
        """交易所維護造成的少量缺漏很常見,不該讓整個系統停擺。"""
        rows = candles(200)
        for i in range(100, 200):
            rows[i][0] += 2 * HOUR_MS       # 只少 2 根
        report = quality.check_ohlcv(rows, "BTC/USDT", "1h", now_ms=now_after(rows))
        self.assertTrue(report.ok, report.summary)
        self.assertIn("MISSING_CANDLES", [w.code for w in report.warnings])

    def test_stale_data_is_rejected(self):
        """最後一根 K 棒太舊代表行情已經停止更新,用它做停損判斷很危險。"""
        rows = candles()
        report = quality.check_ohlcv(
            rows, "BTC/USDT", "1h", now_ms=rows[-1][0] + 10 * HOUR_MS,
        )
        self.assertFalse(report.ok)
        self.assertIn("STALE_DATA", [e.code for e in report.errors])

    def test_fresh_data_is_not_stale(self):
        rows = candles()
        report = quality.check_ohlcv(
            rows, "BTC/USDT", "1h", now_ms=rows[-1][0] + 2 * HOUR_MS,
        )
        self.assertNotIn("STALE_DATA", [e.code for e in report.errors])

    def test_unknown_timeframe_skips_interval_checks(self):
        rows = candles()
        report = quality.check_ohlcv(rows, "BTC/USDT", "7m", now_ms=now_after(rows))
        self.assertNotIn("MISSING_CANDLES", [i.code for i in report.issues])
        self.assertNotIn("STALE_DATA", [i.code for i in report.issues])


class TestValueProblems(unittest.TestCase):

    def test_invalid_ohlc_relationship_is_rejected(self):
        """high 低於 low 代表資料來源壞掉,不是市場現象。"""
        rows = candles()
        rows[20][2], rows[20][3] = 50.0, 200.0
        report = quality.check_ohlcv(rows, "BTC/USDT", "1h", now_ms=now_after(rows))
        self.assertFalse(report.ok)
        self.assertIn("INVALID_OHLC", [e.code for e in report.errors])

    def test_extreme_outlier_is_rejected(self):
        rows = candles()
        rows[30][4] = rows[29][4] * 10      # 收盤價突然變 10 倍
        rows[30][2] = rows[30][4] * 1.001
        report = quality.check_ohlcv(rows, "BTC/USDT", "1h", now_ms=now_after(rows))
        self.assertFalse(report.ok)
        self.assertIn("OUTLIER", [e.code for e in report.errors])

    def test_normal_crypto_volatility_is_not_an_outlier(self):
        """加密貨幣本來就會大幅波動,20% 的單根變動不該被當成壞資料。"""
        rows = candles()
        rows[30][4] = rows[29][4] * 1.20
        rows[30][2] = rows[30][4] * 1.001
        report = quality.check_ohlcv(rows, "BTC/USDT", "1h", now_ms=now_after(rows))
        self.assertNotIn("OUTLIER", [e.code for e in report.errors])

    def test_zero_volume_window_is_rejected(self):
        rows = candles(volume=0.0)
        report = quality.check_ohlcv(rows, "BTC/USDT", "1h", now_ms=now_after(rows))
        self.assertFalse(report.ok)
        self.assertIn("ZERO_VOLUME", [e.code for e in report.errors])

    def test_sparse_volume_is_only_a_warning(self):
        rows = candles()
        for i in range(0, 40):
            rows[i][5] = 0.0
        report = quality.check_ohlcv(rows, "BTC/USDT", "1h", now_ms=now_after(rows))
        self.assertTrue(report.ok, report.summary)
        self.assertIn("SPARSE_VOLUME", [w.code for w in report.warnings])


class TestTickerQuality(unittest.TestCase):

    def _ticker(self, **overrides):
        ticker = {"price": 100.0, "bid": 99.99, "ask": 100.01, "timestamp": BASE_TS}
        ticker.update(overrides)
        return ticker

    def test_valid_ticker_passes(self):
        report = quality.check_ticker(self._ticker(), "BTC/USDT", now_ms=BASE_TS + 1000)
        self.assertTrue(report.ok, report.summary)

    def test_missing_ticker_is_rejected(self):
        self.assertFalse(quality.check_ticker(None, "BTC/USDT").ok)

    def test_missing_price_is_rejected(self):
        report = quality.check_ticker(self._ticker(price=None), "BTC/USDT")
        self.assertFalse(report.ok)
        self.assertEqual(report.errors[0].code, "NO_PRICE")

    def test_zero_price_is_rejected(self):
        report = quality.check_ticker(self._ticker(price=0), "BTC/USDT")
        self.assertFalse(report.ok)

    def test_crossed_book_is_rejected(self):
        """ask 低於 bid 不可能是真實報價。"""
        report = quality.check_ticker(
            self._ticker(bid=100.5, ask=99.5), "BTC/USDT", now_ms=BASE_TS + 1000,
        )
        self.assertFalse(report.ok)
        self.assertIn("CROSSED_BOOK", [e.code for e in report.errors])

    def test_wide_spread_is_only_a_warning(self):
        report = quality.check_ticker(
            self._ticker(bid=100.0, ask=103.0), "BTC/USDT", now_ms=BASE_TS + 1000,
        )
        self.assertTrue(report.ok)
        self.assertIn("WIDE_SPREAD", [w.code for w in report.warnings])

    def test_stale_ticker_is_rejected(self):
        report = quality.check_ticker(
            self._ticker(), "BTC/USDT", max_age_seconds=60, now_ms=BASE_TS + 300_000,
        )
        self.assertFalse(report.ok)
        self.assertIn("STALE_TICKER", [e.code for e in report.errors])


class TestReportShape(unittest.TestCase):

    def test_warnings_do_not_block(self):
        report = quality.QualityReport(symbol="BTC/USDT", timeframe="1h")
        report.add("SPARSE_VOLUME", quality.SEVERITY_WARNING, "流動性偏低")
        self.assertTrue(report.ok)

    def test_any_error_blocks(self):
        report = quality.QualityReport(symbol="BTC/USDT", timeframe="1h")
        report.add("SPARSE_VOLUME", quality.SEVERITY_WARNING, "流動性偏低")
        report.add("STALE_DATA", quality.SEVERITY_ERROR, "資料過期")
        self.assertFalse(report.ok)

    def test_to_dict_is_serializable(self):
        import json
        rows = candles()
        report = quality.check_ohlcv(rows, "BTC/USDT", "1h", now_ms=now_after(rows))
        json.dumps(report.to_dict())
