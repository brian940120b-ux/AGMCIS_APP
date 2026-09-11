"""
CacheService 與 market_data.py 整合測試

重點驗證:
  1. TTL 內重複呼叫不會真的再打一次 API(靠 call_count 驗證)
  2. TTL 過期後會重新抓
  3. API 短暫失敗時,若有舊快取,寧可回傳舊值也不整個掛掉(對交易系統是關鍵行為)
  4. market_data.get_market_snapshot 在資金費率/持倉量任一項失敗時,仍回傳其餘欄位而不整體失敗

注意:market_data.py 自 Phase 2 起是 agmcis/data/market_data.py 的 re-export shim,
所以要 patch 的是實作模組,patch shim 不會生效。
"""
import sys
import os
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cache_service import CacheService  # noqa: E402


class TestCacheService(unittest.TestCase):

    def test_hits_cache_within_ttl(self):
        c = CacheService()
        fetch = MagicMock(return_value=123)

        v1 = c.get_or_fetch("k", 5, fetch)
        v2 = c.get_or_fetch("k", 5, fetch)

        self.assertEqual(v1, 123)
        self.assertEqual(v2, 123)
        fetch.assert_called_once()

    def test_refetches_after_ttl_expires(self):
        c = CacheService()
        fetch = MagicMock(side_effect=[1, 2])

        v1 = c.get_or_fetch("k", 0.05, fetch)
        time.sleep(0.08)
        v2 = c.get_or_fetch("k", 0.05, fetch)

        self.assertEqual(v1, 1)
        self.assertEqual(v2, 2)
        self.assertEqual(fetch.call_count, 2)

    def test_returns_stale_value_when_fetch_fails(self):
        c = CacheService()
        fetch = MagicMock(side_effect=[42, RuntimeError("api down")])

        v1 = c.get_or_fetch("k", 0.05, fetch)
        time.sleep(0.08)
        v2 = c.get_or_fetch("k", 0.05, fetch)  # 過期且會失敗,但應該吐回舊值 42

        self.assertEqual(v1, 42)
        self.assertEqual(v2, 42)

    def test_raises_when_no_stale_value_and_fetch_fails(self):
        c = CacheService()
        fetch = MagicMock(side_effect=RuntimeError("api down"))
        with self.assertRaises(RuntimeError):
            c.get_or_fetch("k", 5, fetch)


class TestMarketDataSnapshot(unittest.TestCase):

    def test_snapshot_survives_partial_failure(self):
        from agmcis.data import market_data

        with patch.object(market_data, "get_ticker", return_value={
            "price": 65000, "change_pct_24h": 1.2, "high_24h": 66000,
            "low_24h": 64000, "volume_24h": 1000,
        }), \
             patch.object(market_data, "get_funding_rate", return_value=None), \
             patch.object(market_data, "get_open_interest", return_value=None), \
             patch.object(market_data, "get_order_book", return_value=None):

            snap = market_data.get_market_snapshot("BTC/USDT")

            self.assertEqual(snap["price"], 65000)
            self.assertIsNone(snap["funding_rate"])
            self.assertIsNone(snap["open_interest"])

    def test_snapshot_survives_unexpected_exception(self):
        """adapter 回傳非預期結構時也不能讓整個快照掛掉,但必須留下記錄。"""
        from agmcis.data import market_data

        with patch.object(market_data, "get_ticker", return_value={"price": 65000}), \
             patch.object(market_data, "get_funding_rate", side_effect=AttributeError("boom")), \
             patch.object(market_data, "get_open_interest", side_effect=RuntimeError("boom")), \
             patch.object(market_data, "get_order_book", side_effect=KeyError("boom")), \
             patch.object(market_data, "logger") as log:

            snap = market_data.get_market_snapshot("BTC/USDT")

            self.assertEqual(snap["price"], 65000)
            self.assertIsNone(snap["funding_rate"])
            self.assertIsNone(snap["open_interest"])
            self.assertIsNone(snap["spread_pct"])
            self.assertEqual(log.warning.call_count, 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
