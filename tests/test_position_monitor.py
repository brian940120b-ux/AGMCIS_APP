"""
持倉監控。

鎖住三個修正:
  1. closed_count 必須是真實平倉數(原本寫死 0,生產 log 一直顯示 monitor=0)。
  2. 取價或停損為 None 不得讓整輪崩掉(原本 None <= float 會拋 TypeError,
     被 scheduler 的 except 吞掉,該輪所有持倉都不檢查停損)。
  3. 缺停損的倉位要出現在 unprotected 清單,不能靜默跳過。
"""
import unittest
from unittest.mock import patch

import position_monitor


def trade(symbol="BTC/USDT", signal="做多", stoploss=97.0, takeprofit=106.0):
    return {
        "symbol": symbol, "signal": signal, "entry_price": 100.0,
        "size_usdt": 1000.0, "leverage": 3.0,
        "stoploss": stoploss, "takeprofit": takeprofit, "status": "OPEN",
    }


def closed_ok(symbol="BTC/USDT"):
    return {
        "success": True,
        "trade": {"symbol": symbol, "signal": "做多", "pnl_pct": 9.0, "pnl_usdt": 90.0},
    }


class TestExitReason(unittest.TestCase):

    def test_long_hits_stop_loss(self):
        self.assertEqual(position_monitor._exit_reason("做多", 96, 97, 106), "自動止損")

    def test_long_hits_take_profit(self):
        self.assertEqual(position_monitor._exit_reason("做多", 107, 97, 106), "自動止盈")

    def test_long_inside_range_stays_open(self):
        self.assertIsNone(position_monitor._exit_reason("做多", 100, 97, 106))

    def test_short_hits_stop_loss(self):
        self.assertEqual(position_monitor._exit_reason("做空", 104, 103, 94), "自動止損")

    def test_short_hits_take_profit(self):
        self.assertEqual(position_monitor._exit_reason("做空", 93, 103, 94), "自動止盈")

    def test_none_stop_loss_does_not_raise(self):
        self.assertIsNone(position_monitor._exit_reason("做多", 96, None, 106))

    def test_none_take_profit_does_not_raise(self):
        self.assertIsNone(position_monitor._exit_reason("做多", 107, 97, None))

    def test_unknown_direction_never_exits(self):
        self.assertIsNone(position_monitor._exit_reason("觀望", 1, 97, 106))


class TestRunPositionMonitor(unittest.TestCase):

    def setUp(self):
        patch.object(position_monitor, "logger").start()
        patch.object(position_monitor, "notify_close_trade").start()
        self.addCleanup(patch.stopall)

    def _run(self, trades, price, close_result=None):
        with patch.object(position_monitor, "get_open_trades", return_value=trades), \
             patch.object(position_monitor, "get_price", return_value=price), \
             patch.object(position_monitor, "close_paper_trade",
                          return_value=close_result or closed_ok()) as close:
            result = position_monitor.run_position_monitor(notify=False)
        return result, close

    def test_reports_real_closed_count(self):
        result, close = self._run([trade()], price=96.0)
        self.assertEqual(result["closed_count"], 1)
        self.assertEqual(len(result["closed"]), 1)
        close.assert_called_once()

    def test_closed_count_is_zero_when_nothing_triggers(self):
        result, close = self._run([trade()], price=100.0)
        self.assertEqual(result["closed_count"], 0)
        close.assert_not_called()

    def test_missing_price_is_skipped_not_crashed(self):
        result, close = self._run([trade()], price=None)
        self.assertEqual(result["closed_count"], 0)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("價格", result["skipped"][0]["reason"])
        close.assert_not_called()

    def test_missing_stop_loss_is_reported_as_unprotected(self):
        result, _ = self._run([trade(stoploss=None)], price=100.0)
        self.assertEqual(len(result["unprotected"]), 1)
        self.assertEqual(result["unprotected"][0]["symbol"], "BTC/USDT")

    def test_unprotected_position_still_honours_take_profit(self):
        result, close = self._run([trade(stoploss=None)], price=107.0)
        self.assertEqual(result["closed_count"], 1)
        self.assertEqual(len(result["unprotected"]), 1)

    def test_unknown_direction_is_skipped(self):
        result, close = self._run([trade(signal="觀望")], price=96.0)
        self.assertEqual(result["closed_count"], 0)
        self.assertEqual(len(result["skipped"]), 1)
        close.assert_not_called()

    def test_already_closed_by_other_path_is_not_counted(self):
        already = {"success": False, "already_closed": True, "message": "沒有可平倉的持倉"}
        result, _ = self._run([trade()], price=96.0, close_result=already)
        self.assertEqual(result["closed_count"], 0)
        self.assertEqual(result["closed"], [])

    def test_no_open_trades_returns_zeros(self):
        result, _ = self._run([], price=100.0)
        self.assertEqual(result["checked"], 0)
        self.assertEqual(result["closed_count"], 0)
