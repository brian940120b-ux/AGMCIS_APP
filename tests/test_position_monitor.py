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
    """_exit_reason 回傳 (原因, 是否強平, 成交價)。"""

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
        reason, liquidated, _ = position_monitor._exit_reason(
            "做多", 85, 99, None, liquidation_price=91,
        )

        self.assertEqual(reason, "自動止損")
        self.assertFalse(liquidated)

    def test_liquidation_wins_when_it_is_closer_than_the_stop(self):
        """停損放得很遠、槓桿又高時,強平才會先到。"""
        reason, liquidated, _ = position_monitor._exit_reason(
            "做多", 90, 80, None, liquidation_price=95,
        )

        self.assertEqual(reason, position_monitor.LIQUIDATION_REASON)
        self.assertTrue(liquidated)

    def test_short_liquidation_is_the_lower_of_the_two(self):
        reason, liquidated, _ = position_monitor._exit_reason(
            "做空", 110, 120, None, liquidation_price=105,
        )

        self.assertEqual(reason, position_monitor.LIQUIDATION_REASON)
        self.assertTrue(liquidated)

    def test_a_position_without_a_stop_can_still_be_liquidated(self):
        """沒有停損的倉位沒有風險上限,但強平還是會發生。"""
        reason, liquidated, _ = position_monitor._exit_reason(
            "做多", 90, None, None, liquidation_price=91,
        )

        self.assertEqual(reason, position_monitor.LIQUIDATION_REASON)
        self.assertTrue(liquidated)

    def test_take_profit_still_works_when_a_liquidation_price_exists(self):
        reason, liquidated, _ = position_monitor._exit_reason(
            "做多", 107, 97, 106, liquidation_price=91,
        )

        self.assertEqual(reason, "自動止盈")
        self.assertFalse(liquidated)

    def test_nothing_triggers_inside_the_safe_range(self):
        reason, _, _ = position_monitor._exit_reason(
            "做多", 100, 97, 106, liquidation_price=91,
        )

        self.assertIsNone(reason)


class TestRunPositionMonitor(unittest.TestCase):

    def setUp(self):
        patch.object(position_monitor, "logger").start()
        patch.object(position_monitor, "notify_close_trade").start()
        self.addCleanup(patch.stopall)

    def _run(self, trades, price, close_result=None, intrabar=(None, None, None)):
        """intrabar 預設關掉 —— 大部分測試驗的是迴圈行為,不是盤中判定。"""
        with patch.object(position_monitor, "get_open_trades", return_value=trades), \
             patch.object(position_monitor, "get_price", return_value=price), \
             patch.object(position_monitor, "_intrabar_range", return_value=intrabar), \
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


class TestIntrabarRemovesTheOptimisticBias(unittest.TestCase):
    """
    兩次輪詢之間穿刺停損又彈回來的行情,交易所的觸發單會成交,
    模擬盤不能假裝沒發生 —— 那會讓勝率系統性偏高。
    """

    def _reason(self, **kwargs):
        return position_monitor._exit_reason(**kwargs)

    def _run(self, intrabar):
        with patch.object(position_monitor, "logger"), \
             patch.object(position_monitor, "notify_close_trade"), \
             patch.object(position_monitor, "get_open_trades",
                          return_value=[trade()]), \
             patch.object(position_monitor, "get_price", return_value=100.0), \
             patch.object(position_monitor, "_intrabar_range",
                          return_value=intrabar), \
             patch.object(position_monitor, "close_paper_trade",
                          return_value=closed_ok()) as close:
            return position_monitor.run_position_monitor(notify=False), close

    def test_a_wick_through_the_stop_counts_even_if_price_recovered(self):
        """現價回到 100,但這段期間曾經跌到 96。那筆已經被掃出場了。"""
        reason, _, fill = self._reason(
            signal="做多", price=100.0, stoploss=97.0, takeprofit=110.0,
            high=101.0, low=96.0,
        )

        self.assertEqual(reason, "自動止損")
        self.assertAlmostEqual(fill, 97.0, places=6)

    def test_a_short_wick_uses_the_high(self):
        reason, _, _ = self._reason(
            signal="做空", price=100.0, stoploss=103.0, takeprofit=90.0,
            high=104.0, low=99.0,
        )

        self.assertEqual(reason, "自動止損")

    def test_a_wick_that_never_reached_the_stop_does_not_exit(self):
        reason, _, _ = self._reason(
            signal="做多", price=100.0, stoploss=97.0, takeprofit=110.0,
            high=101.0, low=98.0,
        )

        self.assertIsNone(reason)

    def test_the_stop_wins_when_both_were_touched(self):
        """
        沒有逐筆資料就不知道誰先到。一律假設停損先到 ——
        寧可低估績效也不要高估(與回測引擎同一個假設)。
        """
        reason, _, _ = self._reason(
            signal="做多", price=100.0, stoploss=97.0, takeprofit=110.0,
            high=115.0, low=96.0,
        )

        self.assertEqual(reason, "自動止損")

    def test_a_gap_through_the_stop_fills_at_the_open(self):
        """
        開盤就已經跌破停損時,不可能還在停損價成交。
        假設成交在停損價會高估績效。
        """
        reason, _, fill = self._reason(
            signal="做多", price=90.0, stoploss=97.0, takeprofit=110.0,
            high=92.0, low=88.0, open_price=91.0,
        )

        self.assertEqual(reason, "自動止損")
        self.assertAlmostEqual(fill, 91.0, places=6)

    def test_a_normal_touch_fills_at_the_trigger_not_the_open(self):
        reason, _, fill = self._reason(
            signal="做多", price=100.0, stoploss=97.0, takeprofit=110.0,
            high=101.0, low=96.0, open_price=100.5,
        )

        self.assertAlmostEqual(fill, 97.0, places=6)

    def test_without_intrabar_data_it_behaves_exactly_as_before(self):
        """降級可以接受。行為要與 Phase 10 相同,不能變得更寬鬆或更嚴。"""
        reason, _, _ = self._reason(
            signal="做多", price=100.0, stoploss=97.0, takeprofit=110.0,
        )

        self.assertIsNone(reason)

    def test_the_run_loop_closes_at_the_trigger_not_the_polled_price(self):
        result, close = self._run((101.0, 96.0, 100.0))

        self.assertEqual(result["closed_count"], 1)
        self.assertEqual(result["intrabar_checked"], 1)
        self.assertAlmostEqual(close.call_args.args[1], 97.0, places=6)

    def test_a_symbol_without_intrabar_data_is_counted_separately(self):
        """取不到 K 棒的那一輪,停損判定比平常寬鬆。那必須看得見。"""
        result, _ = self._run((None, None, None))

        self.assertEqual(result["intrabar_checked"], 0)


class TestIntrabarFetchDegradesLoudly(unittest.TestCase):
    """降級可以接受,安靜地降級不行。"""

    def test_a_failed_fetch_returns_nothing_and_logs(self):
        with patch.object(position_monitor.settings,
                          "POSITION_MONITOR_USE_INTRABAR", True), \
             patch("agmcis.data.market_data.get_ohlcv_dicts",
                   side_effect=RuntimeError("斷線")), \
             patch.object(position_monitor, "logger") as logger:
            result = position_monitor._intrabar_range("BTC/USDT")

        self.assertEqual(result, (None, None, None))
        logger.warning.assert_called()

    def test_empty_candles_are_reported_not_silently_ignored(self):
        with patch.object(position_monitor.settings,
                          "POSITION_MONITOR_USE_INTRABAR", True), \
             patch("agmcis.data.market_data.get_ohlcv_dicts", return_value=[]), \
             patch.object(position_monitor, "logger") as logger:
            result = position_monitor._intrabar_range("BTC/USDT")

        self.assertEqual(result, (None, None, None))
        logger.warning.assert_called()

    def test_it_is_skipped_entirely_when_disabled(self):
        with patch.object(position_monitor.settings,
                          "POSITION_MONITOR_USE_INTRABAR", False), \
             patch("agmcis.data.market_data.get_ohlcv_dicts") as fetch:
            result = position_monitor._intrabar_range("BTC/USDT")

        self.assertEqual(result, (None, None, None))
        fetch.assert_not_called()

    def test_it_reduces_candles_to_the_extremes(self):
        candles = [
            {"open": 100.0, "high": 101.0, "low": 99.0},
            {"open": 100.5, "high": 103.0, "low": 96.0},
            {"open": 97.0, "high": 98.0, "low": 97.0},
        ]

        with patch.object(position_monitor.settings,
                          "POSITION_MONITOR_USE_INTRABAR", True), \
             patch("agmcis.data.market_data.get_ohlcv_dicts",
                   return_value=candles):
            high, low, first_open = position_monitor._intrabar_range("BTC/USDT")

        self.assertEqual((high, low, first_open), (103.0, 96.0, 100.0))
