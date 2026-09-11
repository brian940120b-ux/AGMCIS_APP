"""
市場總覽的資料誠實性。

Phase 0 稽核的核心問題之一:系統分不出「市場中性」與「資料壞掉」。
舊版 market_center 抓不到行情就把價格與漲跌幅塞 0,
於是交易所整個掛掉時,報告會很有自信地說「市場中性」。
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_report
import market_center


def ticker(symbol, price=100.0, change=1.0, volume=1_000_000.0):
    return {
        "symbol": symbol,
        "price": price,
        "change_pct_24h": change,
        "quote_volume_24h": volume,
    }


class TestDataSource(unittest.TestCase):

    def test_prices_come_from_the_agmcis_data_layer_not_binance(self):
        """
        實際下單在 BingX,行情就必須是 BingX 的。
        舊版用 ccxt.binance() —— 價格、成交量、漲跌幅全都不是成交環境的數字。
        """
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "market_center.py",
        )
        with open(path, encoding="utf-8") as handle:
            source = handle.read()

        self.assertNotIn("ccxt.binance", source)
        self.assertIn("from agmcis.data import market_data", source)


class TestMissingDataIsNotZero(unittest.TestCase):

    def test_unavailable_symbol_is_flagged_not_zeroed(self):
        with patch.object(market_data_module(), "get_ticker", return_value=None):
            result = market_center.get_market_center(["BTC/USDT"])

        item = result["market_data"][0]
        self.assertFalse(item["available"])
        self.assertIsNone(item["price"])
        self.assertIsNone(item["change_24h"])

    def test_total_outage_reports_unknown_not_neutral(self):
        """全部抓不到時必須說「資料不足」,不能說「中性」。"""
        with patch.object(market_data_module(), "get_ticker", return_value=None):
            result = market_center.get_market_center(
                ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
            )

        self.assertEqual(result["market_sentiment"], market_center.SENTIMENT_UNKNOWN)
        self.assertIsNone(result["market_score"])
        self.assertIsNone(result["best_symbol"])

    def test_partial_outage_below_the_threshold_also_reports_unknown(self):
        symbols = ["A/USDT", "B/USDT", "C/USDT", "D/USDT", "E/USDT"]

        def fake(symbol, *args, **kwargs):
            return ticker(symbol) if symbol in ("A/USDT", "B/USDT") else None

        with patch.object(market_data_module(), "get_ticker", side_effect=fake):
            result = market_center.get_market_center(symbols)

        self.assertEqual(result["available_count"], 2)
        self.assertEqual(result["market_sentiment"], market_center.SENTIMENT_UNKNOWN)

    def test_broken_symbols_do_not_pollute_the_ranking(self):
        """抓不到的標的若當成 0% 參與排序,會憑空排在下跌標的前面。"""
        def fake(symbol, *args, **kwargs):
            if symbol == "BROKEN/USDT":
                return None
            return ticker(symbol, change=-5.0)

        with patch.object(market_data_module(), "get_ticker", side_effect=fake):
            result = market_center.get_market_center(
                ["A/USDT", "B/USDT", "C/USDT", "BROKEN/USDT"],
            )

        ranked = [item["symbol"] for item in result["gainers"]]
        self.assertNotIn("BROKEN/USDT", ranked)


class TestSentimentWithGoodData(unittest.TestCase):

    def _run(self, changes):
        symbols = [f"S{i}/USDT" for i in range(len(changes))]
        mapping = dict(zip(symbols, changes))

        def fake(symbol, *args, **kwargs):
            return ticker(symbol, change=mapping[symbol])

        with patch.object(market_data_module(), "get_ticker", side_effect=fake):
            return market_center.get_market_center(symbols)

    def test_mostly_up_is_bullish(self):
        self.assertEqual(self._run([3, 2, 1, 1, -1])["market_sentiment"], "偏多")

    def test_mostly_down_is_bearish(self):
        self.assertEqual(self._run([-3, -2, -1, -1, 1])["market_sentiment"], "偏空")

    def test_mixed_is_neutral(self):
        self.assertEqual(self._run([1, 1, -1, -1, -1])["market_sentiment"], "中性")

    def test_best_symbol_is_the_biggest_gainer(self):
        result = self._run([1, 5, 2, 3, 4])
        self.assertEqual(result["best_symbol"], "S1/USDT")


class TestAiReportRefusesToGuess(unittest.TestCase):

    def test_report_says_data_is_missing_instead_of_recommending(self):
        unknown = {
            "market_sentiment": market_center.SENTIMENT_UNKNOWN,
            "market_score": None,
            "best_symbol": None,
            "available_count": 0,
            "requested_count": 5,
        }

        with patch.object(ai_report, "get_market_center", return_value=unknown), \
             patch.object(ai_report, "get_crypto_news", return_value=[]):
            report = ai_report.generate_ai_report()

        self.assertFalse(report["data_available"])
        self.assertIn("資料不足", report["recommendation"])
        self.assertNotIn("None", report["recommendation"])

    def test_news_failure_does_not_kill_the_whole_report(self):
        good = {
            "market_sentiment": "偏多",
            "market_score": 80,
            "best_symbol": "BTC/USDT",
            "available_count": 5,
            "requested_count": 5,
        }

        with patch.object(ai_report, "get_market_center", return_value=good), \
             patch.object(ai_report, "get_crypto_news",
                          side_effect=RuntimeError("news down")):
            report = ai_report.generate_ai_report()

        self.assertTrue(report["data_available"])
        self.assertEqual(report["headlines"], [])
        self.assertIn("BTC/USDT", report["recommendation"])


def market_data_module():
    from agmcis.data import market_data
    return market_data


if __name__ == "__main__":
    unittest.main()
