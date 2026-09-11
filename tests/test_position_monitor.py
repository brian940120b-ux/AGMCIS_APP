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
    """Phase 10 起 _exit_reason 回傳 (原因, 是否強平)。"""

    def _reason(self, *args, **kwargs):
        return position_monitor._exit_reason(*args, **kwargs)[0]

    def test_long_hits_stop_loss(self):
        self.assertEqual(self._reason("做多", 96, 97, 106), "自動止損")

    def test_long_hits_take_profit(self):
        self.assertEqual(self._reason("做多", 107, 97, 106), "自動止盈")

    def test_long_inside_range_stays_open(self):
        self.assertIsNone(self._reason("做多", 100, 97, 106))

    def test_short_hits_stop_loss(self):
        self.assertEqual(self._reason("做空", 104, 103, 94), "自動止損")

    def test_short_hits_take_profit(self):
        self.assertEqual(self._reason("做空", 93, 103, 94), "自動止盈")

    def test_none_stop_loss_does_not_raise(self):
        self.assertIsNone(self._reason("做多", 96, None, 106))

    def test_none_take_profit_does_not_raise(self):
        self.assertIsNone(self._reason("做多", 107, 97, None))

    def test_unknown_direction_never_exits(self):
        self.assertIsNone(self._reason("觀望", 1, 97, 106))


class TestLiquidationOrdering(unittest.TestCase):
    """
    Phase 10。順序與回測引擎一致:停損與強平之中,
    **離進場價較近的那個先觸發**,不是無條件先看強平。
    """

    def test_a_normal_stop_is_not_reported_as_a_liquidation(self):
        """做多停損 99、強平 91,價格跌到 85 —— 先經過 99,那是停損。"""
        reason, liquidated = position_monitor._exit_reason(
            "做多", 85, 99, None, liquidation_price=91,
        )

        self.assertEqual(reason, "自動止損")
        self.assertFalse(liquidated)

    def test_liquidation_wins_when_it_is_closer_than_the_stop(self):
        """停損放得很遠、槓桿又高時,強平才會先到。"""
        reason, liquidated = position_monitor._exit_reason(
            "做多", 90, 80, None, liquidation_price=95,
        )

        self.assertEqual(reason, position_monitor.LIQUIDATION_REASON)
        self.assertTrue(liquidated)

    def test_short_liquidation_is_the_lower_of_the_two(self):
        reason, liquidated = position_monitor._exit_reason(
            "做空", 110, 120, None, liquidation_price=105,
        )

        self.assertEqual(reason, position_monitor.LIQUIDATION_REASON)
        self.assertTrue(liquidated)

    def test_a_position_without_a_stop_can_still_be_liquidated(self):
        """沒有停損的倉位沒有風險上限,但強平還是會發生。"""
        reason, liquidated = position_monitor._exit_reason(
            "做多", 90, None, None, liquidation_price=91,
        )

        self.assertEqual(reason, position_monitor.LIQUIDATION_REASON)
        self.assertTrue(liquidated)

    def test_take_profit_still_works_when_a_liquidation_price_exists(self):
        reason, liquidated = position_monitor._exit_reason(
            "做多", 107, 97, 106, liquidation_price=91,
        )

        self.assertEqual(reason, "自動止盈")
        self.assertFalse(liquidated)

    def test_nothing_triggers_inside_the_safe_range(self):
        reason, _ = position_monitor._exit_reason(
            "做多", 100, 97, 106, liquidation_price=91,
        )

        self.assertIsNone(reason)


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
