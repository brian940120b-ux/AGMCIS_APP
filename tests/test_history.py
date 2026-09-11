"""
分頁抓取歷史 K 棒。

樣本數是現在擋住驗證的真正原因:1h 的 1500 根只有 62 天,
切掉 30% 樣本外之後,live 訊號管線大約只交易 28 筆 ——
剛好卡在 Phase 8 的 30 筆門檻底下。

這一層最重要的不是「抓得到」,而是**抓來的東西沒有被悄悄弄髒**:
缺口不補、重複不留、分頁不原地打轉。
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.data import history

HOUR = 3_600_000


def candles(start_ms, count, step=HOUR, price=100.0):
    return [
        [start_ms + i * step, price, price + 1, price - 1, price + 0.5, 10.0]
        for i in range(count)
    ]


class FakeAdapter:
    """
    模擬一個有固定歷史長度的交易所。

    最早的資料在 `earliest` 之前不存在 —— 真實交易所就是這樣,
    而「抓到底了」這件事必須被正確偵測到。
    """

    def __init__(self, total=3000, page_size=1000, earliest=0, supports_since=True):
        self.all = candles(earliest, total)
        self.page_size = page_size
        self.supports_since = supports_since
        self.calls = []

    def get_ohlcv(self, symbol, timeframe, limit=150, market_type=None):
        self.calls.append(("latest", None))
        return self.all[-min(limit, self.page_size):]

    # history._fetch_since 會走這兩個
    def _instance(self, market_type):
        return self

    def to_market_symbol(self, symbol, market_type):
        return symbol

    def fetch_ohlcv(self, symbol, timeframe, since, limit):
        self.calls.append(("since", since))

        if not self.supports_since:
            # 不支援 since 的交易所會忽略它,回傳最近的那一段
            return self.all[-limit:]

        rows = [row for row in self.all if row[0] >= since]
        return rows[:limit]


def fetch(adapter, total=2500, **kwargs):
    directory = tempfile.mkdtemp()
    with patch.object(history, "CACHE_DIR", directory):
        return history.fetch(
            "BTC/USDT", timeframe="1h", total=total,
            adapter=adapter, sleep=lambda _: None, **kwargs
        )


class TestGapDetection(unittest.TestCase):
    """
    兩段之間少了 12 小時,直接串起來會讓回測看到一根
    「12 小時內漲 3%」的假 K 棒 —— 那個價格跳動從來沒有發生過,
    但停損會照著它被觸發。
    """

    def test_a_continuous_series_has_no_gaps(self):
        self.assertEqual(history.find_gaps(candles(0, 100), "1h"), [])

    def test_a_missing_stretch_is_reported(self):
        rows = candles(0, 50) + candles(50 * HOUR + 10 * HOUR, 50)
        gaps = history.find_gaps(rows, "1h")

        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["missing"], 10)

    def test_a_single_missing_candle_is_tolerated(self):
        """交易所偶爾會漏一根,那不值得讓整段資料作廢。"""
        rows = candles(0, 50) + candles(51 * HOUR, 50)

        self.assertEqual(history.find_gaps(rows, "1h"), [])

    def test_gaps_are_never_filled_in(self):
        """
        補值會製造出一段「什麼都沒發生」的假歷史,
        而那段假歷史會被當成真的拿去算勝率。
        """
        adapter = FakeAdapter(total=2000)
        # 挖一個洞
        del adapter.all[500:520]

        with patch.object(history, "logger"):
            result = fetch(adapter, total=2000)

        self.assertTrue(result.has_gaps)
        # 洞還在 —— 沒有被填平
        self.assertLess(result.count, 2000)


class TestDeduplication(unittest.TestCase):

    def test_overlapping_pages_do_not_double_count(self):
        """分頁的邊界常常重疊。同一根算兩次會讓指標與交易筆數都失真。"""
        rows = candles(0, 100) + candles(50 * HOUR, 100)
        deduped, removed = history._dedupe(rows)

        self.assertEqual(len(deduped), 150)
        self.assertEqual(removed, 50)

    def test_the_result_is_sorted_by_time(self):
        rows = candles(50 * HOUR, 20) + candles(0, 20)
        deduped, _ = history._dedupe(rows)

        stamps = [row[0] for row in deduped]
        self.assertEqual(stamps, sorted(stamps))

    def test_the_fetch_reports_how_many_were_removed(self):
        adapter = FakeAdapter(total=3000)

        result = fetch(adapter, total=2500)

        self.assertGreaterEqual(result.duplicates_removed, 0)
        self.assertEqual(len(result.candles), len(set(c[0] for c in result.candles)))


class TestPagination(unittest.TestCase):

    def test_it_fetches_more_than_one_page_worth(self):
        """這就是整個模組存在的理由。"""
        adapter = FakeAdapter(total=5000, page_size=1000)

        result = fetch(adapter, total=3000)

        self.assertGreaterEqual(result.count, 2500)
        self.assertGreater(result.pages_fetched, 1)

    def test_it_stops_when_the_exchange_runs_out_of_history(self):
        """
        交易所不再給更早的資料時會一直回同一段。
        沒有停止條件的話會打滿 MAX_PAGES 次白工。
        """
        adapter = FakeAdapter(total=1500, page_size=1000)

        with patch.object(history, "logger"):
            result = fetch(adapter, total=10000)

        self.assertLess(result.pages_fetched, history.MAX_PAGES)
        self.assertLessEqual(result.count, 1500)

    def test_an_adapter_that_ignores_since_does_not_spin(self):
        """
        不支援 since 的交易所會一直回最近的那一段。
        看起來像在分頁,實際上原地打轉。
        """
        adapter = FakeAdapter(total=5000, supports_since=False)

        with patch.object(history, "logger"):
            result = fetch(adapter, total=4000)

        self.assertLess(result.pages_fetched, history.MAX_PAGES)

    def test_a_failing_page_does_not_lose_what_was_already_fetched(self):
        adapter = FakeAdapter(total=5000)
        original = adapter.fetch_ohlcv

        def flaky(symbol, timeframe, since, limit):
            raise ConnectionError("斷線")

        adapter.fetch_ohlcv = flaky

        with patch.object(history, "logger"):
            result = fetch(adapter, total=3000)

        self.assertGreater(result.count, 0)

    def test_it_returns_the_most_recent_candles(self):
        """往回抓:拿到的要是「最近的 N 根」,不是某段隨機的歷史。"""
        adapter = FakeAdapter(total=5000)

        result = fetch(adapter, total=2000)

        self.assertEqual(result.candles[-1][0], adapter.all[-1][0])


class TestCache(unittest.TestCase):
    """Lab 會重跑很多次,而歷史 K 棒是不會變的。"""

    def test_a_second_fetch_uses_the_cache(self):
        directory = tempfile.mkdtemp()
        adapter = FakeAdapter(total=3000)

        with patch.object(history, "CACHE_DIR", directory):
            first = history.fetch("BTC/USDT", total=2000, adapter=adapter,
                                  sleep=lambda _: None)
            calls_after_first = len(adapter.calls)

            second = history.fetch("BTC/USDT", total=2000, adapter=adapter,
                                   sleep=lambda _: None)

        self.assertFalse(first.from_cache)
        self.assertTrue(second.from_cache)
        self.assertEqual(len(adapter.calls), calls_after_first)
        self.assertEqual(first.count, second.count)

    def test_a_larger_request_is_not_served_from_a_smaller_cache(self):
        directory = tempfile.mkdtemp()
        adapter = FakeAdapter(total=6000)

        with patch.object(history, "CACHE_DIR", directory):
            history.fetch("BTC/USDT", total=1500, adapter=adapter,
                          sleep=lambda _: None)
            bigger = history.fetch("BTC/USDT", total=4000, adapter=adapter,
                                   sleep=lambda _: None)

        self.assertFalse(bigger.from_cache)
        self.assertGreater(bigger.count, 1500)

    def test_a_corrupt_cache_falls_back_to_fetching(self):
        directory = tempfile.mkdtemp()
        adapter = FakeAdapter(total=3000)

        with patch.object(history, "CACHE_DIR", directory):
            path = history._cache_path("BTC/USDT", "1h", "perpetual")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("這不是 JSON")

            with patch.object(history, "logger") as logger:
                result = history.fetch("BTC/USDT", total=2000, adapter=adapter,
                                       sleep=lambda _: None)

        self.assertFalse(result.from_cache)
        self.assertGreater(result.count, 0)
        logger.warning.assert_called()

    def test_use_cache_false_always_fetches(self):
        directory = tempfile.mkdtemp()
        adapter = FakeAdapter(total=3000)

        with patch.object(history, "CACHE_DIR", directory):
            history.fetch("BTC/USDT", total=2000, adapter=adapter,
                          sleep=lambda _: None)
            second = history.fetch("BTC/USDT", total=2000, adapter=adapter,
                                   sleep=lambda _: None, use_cache=False)

        self.assertFalse(second.from_cache)


class TestReporting(unittest.TestCase):

    def test_the_span_is_reported_in_days(self):
        adapter = FakeAdapter(total=3000)

        result = fetch(adapter, total=2400)

        # 2400 根 1h ≈ 100 天
        self.assertGreater(result.span_days, 90)

    def test_an_unknown_timeframe_is_rejected_rather_than_guessed(self):
        with self.assertRaises(ValueError):
            history.fetch("BTC/USDT", timeframe="3 個月",
                          adapter=FakeAdapter(), sleep=lambda _: None)

    def test_the_summary_mentions_gaps(self):
        adapter = FakeAdapter(total=2000)
        del adapter.all[500:520]

        with patch.object(history, "logger"):
            result = fetch(adapter, total=2000)

        self.assertTrue(any("缺口" in line for line in result.summary_lines()))


class TestLabUsesPagination(unittest.TestCase):

    def test_a_large_limit_goes_through_the_paginated_path(self):
        from agmcis.backtest import legacy

        with patch.object(legacy, "_load_paginated") as paginated:
            legacy.load_data("BTC/USDT", timeframe="1h", limit=5000)

        paginated.assert_called_once()

    def test_a_small_limit_uses_the_single_request_path(self):
        from agmcis.backtest import legacy

        class Report:
            summary = "ok"

        import pandas as pd

        frame = pd.DataFrame(candles(0, 200),
                             columns=["timestamp", "open", "high", "low",
                                      "close", "volume"])

        with patch.object(legacy.market_data, "get_ohlcv_checked",
                          return_value=(frame, Report())) as single, \
             patch.object(legacy, "_load_paginated") as paginated:
            legacy.load_data("BTC/USDT", timeframe="1h", limit=500)

        single.assert_called_once()
        paginated.assert_not_called()


if __name__ == "__main__":
    unittest.main()
