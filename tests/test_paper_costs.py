"""
模擬盤成本(Phase 10)。

Phase 10 之前模擬損益完全不含成本:沒有手續費、沒有滑點、沒有點差、
沒有資金費用、沒有強平。那種數字是理想化的上界。

一個沒有成本的模擬盤最危險的地方不是「數字偏高」,而是它會讓
**本來沒有優勢的策略看起來有優勢** —— 一天進出好幾次的策略,
光手續費就能吃掉全部價差。
"""
import os
import sys
import unittest
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database_service
import paper_trading
from agmcis.backtest.costs import ZERO_COSTS, CostModel
from agmcis.execution import paper_costs
from tests.test_pnl_leverage import FakeCursor, fake_transaction, trade_row


class TestCostModelIsSharedWithTheBacktest(unittest.TestCase):
    """
    兩邊用不同的成本假設,模擬盤就沒辦法驗證回測 —— 而那正是模擬盤的用途。
    費率不同時,你分不出「策略不行」與「成本假設不同」。
    """

    def test_it_is_the_same_class_the_backtest_uses(self):
        self.assertIsInstance(paper_costs.build_cost_model(), CostModel)

    def test_every_rate_comes_from_settings(self):
        from agmcis.config import settings

        model = paper_costs.build_cost_model()

        self.assertEqual(model.taker_fee, settings.PAPER_TAKER_FEE)
        self.assertEqual(model.slippage_pct, settings.PAPER_SLIPPAGE_PCT)
        self.assertEqual(model.spread_pct, settings.PAPER_SPREAD_PCT)
        self.assertEqual(model.funding_rate_8h, settings.PAPER_FUNDING_RATE_8H)


class TestFillPriceIsAlwaysAdverse(unittest.TestCase):
    """成交價永遠在對自己不利的一邊。這不是悲觀,這是市價單的實際行為。"""

    def test_buying_to_open_a_long_pays_up(self):
        self.assertGreater(paper_costs.fill_price(100, True, is_entry=True), 100)

    def test_selling_to_close_a_long_gets_less(self):
        self.assertLess(paper_costs.fill_price(100, True, is_entry=False), 100)

    def test_selling_to_open_a_short_gets_less(self):
        self.assertLess(paper_costs.fill_price(100, False, is_entry=True), 100)

    def test_buying_to_close_a_short_pays_up(self):
        self.assertGreater(paper_costs.fill_price(100, False, is_entry=False), 100)

    def test_a_round_trip_always_costs_something(self):
        entry = paper_costs.fill_price(100, True, is_entry=True)
        exit_ = paper_costs.fill_price(100, True, is_entry=False)

        self.assertLess(exit_, entry)


class TestLiquidationPrice(unittest.TestCase):

    def test_higher_leverage_moves_liquidation_closer(self):
        near = paper_costs.liquidation_price(100, 20, True)
        far = paper_costs.liquidation_price(100, 2, True)

        self.assertGreater(near, far)

    def test_shorts_liquidate_above_the_entry(self):
        self.assertGreater(paper_costs.liquidation_price(100, 10, False), 100)

    def test_it_matches_the_backtest_formula(self):
        from agmcis.backtest.engine import MAINTENANCE_MARGIN_RATIO

        expected = 100 * (1 - (1 - MAINTENANCE_MARGIN_RATIO) / 10)

        self.assertAlmostEqual(
            paper_costs.liquidation_price(100, 10, True, mmr=MAINTENANCE_MARGIN_RATIO),
            expected, places=8,
        )

    def test_zero_leverage_has_no_liquidation_price(self):
        self.assertIsNone(paper_costs.liquidation_price(100, 0, True))


class TestCostsReduceRealisedPnl(unittest.TestCase):

    def _close(self, costs, entry=100.0, exit_price=110.0, size=1000.0,
               leverage=5.0, entry_fee=0.0, held_seconds=0.0, liquidated=False,
               signal="做多"):
        cursor = FakeCursor(
            trade_row=trade_row(signal=signal, entry=entry, size=size,
                                leverage=leverage, entry_fee=entry_fee,
                                held_seconds=held_seconds),
            account_row=(10000.0, 0, 0, 0),
        )
        with patch.object(database_service, "transaction",
                          lambda: fake_transaction(cursor)):
            return database_service.close_trade_atomic(
                "BTC/USDT", exit_price, "測試", costs=costs, liquidated=liquidated,
            )

    def test_fees_make_the_result_worse_than_the_cost_free_case(self):
        free = self._close(None)
        charged = self._close(paper_costs.build_cost_model())

        self.assertLess(charged["pnl_usdt"], free["pnl_usdt"])

    def test_gross_and_net_are_both_recorded(self):
        result = self._close(paper_costs.build_cost_model())

        self.assertGreater(result["gross_pnl_usdt"], result["pnl_usdt"])
        self.assertGreater(result["exit_fee"], 0)

    def test_entry_fee_charged_at_open_is_deducted_at_close(self):
        """成本在平倉一次結算。總額必須正確,不能漏掉開倉時算好的那一筆。"""
        without = self._close(ZERO_COSTS, entry_fee=0.0)
        with_fee = self._close(ZERO_COSTS, entry_fee=2.5)

        self.assertAlmostEqual(
            without["pnl_usdt"] - with_fee["pnl_usdt"], 2.5, places=4,
        )

    def test_funding_accrues_with_time_held(self):
        model = paper_costs.build_cost_model()

        short_hold = self._close(model, held_seconds=3600)
        long_hold = self._close(model, held_seconds=3600 * 72)

        self.assertGreater(long_hold["funding_usdt"], short_hold["funding_usdt"])

    def test_shorts_receive_funding_when_the_rate_is_positive(self):
        """費率為正時做多付、做空收。方向搞反會讓空單的成本被高估。"""
        model = paper_costs.build_cost_model()

        long_side = self._close(model, held_seconds=3600 * 24, signal="做多")
        short_side = self._close(model, held_seconds=3600 * 24, signal="做空",
                                 entry=100.0, exit_price=90.0)

        self.assertGreater(long_side["funding_usdt"], 0)
        self.assertLess(short_side["funding_usdt"], 0)

    def test_funding_is_zero_for_an_instantly_closed_trade(self):
        result = self._close(paper_costs.build_cost_model(), held_seconds=0)

        self.assertAlmostEqual(result["funding_usdt"], 0.0, places=8)

    def test_liquidation_adds_a_liquidation_fee(self):
        model = paper_costs.build_cost_model()

        normal = self._close(model, exit_price=91.0)
        liquidated = self._close(model, exit_price=91.0, liquidated=True)

        self.assertGreater(liquidated["exit_fee"], normal["exit_fee"])
        self.assertTrue(liquidated["liquidated"])

    def test_a_loss_never_exceeds_the_margin(self):
        """強平就是為了這件事。"""
        result = self._close(paper_costs.build_cost_model(),
                             entry=100.0, exit_price=50.0, size=1000.0,
                             leverage=10.0, liquidated=True)

        self.assertGreaterEqual(result["pnl_usdt"], -1000.0)

    def test_the_cost_basis_is_recorded_so_rows_can_be_told_apart(self):
        """
        不含成本的歷史資料與含成本的新資料混在一起平均,
        「加了成本之後績效差多少」這個問題就永遠問不出答案。
        """
        self.assertEqual(self._close(None)["cost_basis"], "NO_COSTS")
        self.assertEqual(
            self._close(paper_costs.build_cost_model())["cost_basis"], "WITH_COSTS",
        )

    def test_liquidated_closes_do_not_also_pay_slippage(self):
        """強平在強平價成交。再加一次滑點等於把同一件事算兩遍。"""
        model = paper_costs.build_cost_model()

        result = self._close(model, exit_price=91.0, liquidated=True)

        self.assertAlmostEqual(result["exit_price"], 91.0, places=8)

    def test_a_normal_close_does_pay_slippage(self):
        model = paper_costs.build_cost_model()

        result = self._close(model, exit_price=110.0, signal="做多")

        self.assertLess(result["exit_price"], 110.0)


class TestOpenRejectsUnsafeCombinations(unittest.TestCase):

    def setUp(self):
        patch.object(paper_trading, "logger").start()
        self.addCleanup(patch.stopall)

    def _open(self, **kwargs):
        fields = dict(symbol="BTC/USDT", entry_price=100.0, signal="做多",
                      size_usdt=100.0, stoploss=97.0, takeprofit=110.0,
                      leverage=3.0)
        fields.update(kwargs)

        with patch.object(paper_trading, "insert_trade", return_value=1) as insert:
            result = paper_trading.create_paper_trade(**fields)
        return result, insert

    def test_the_recorded_entry_price_is_the_fill_not_the_quote(self):
        result, _ = self._open()

        trade = result["trade"]
        self.assertGreater(trade["entry_price"], trade["requested_entry_price"])

    def test_an_entry_fee_is_recorded(self):
        result, _ = self._open()

        self.assertGreater(result["trade"]["entry_fee"], 0)

    def test_a_liquidation_price_is_recorded(self):
        result, _ = self._open()

        self.assertIsNotNone(result["trade"]["liquidation_price"])
        self.assertLess(result["trade"]["liquidation_price"], 100.0)

    def test_a_stop_that_slippage_pushes_to_the_wrong_side_is_rejected(self):
        """
        滑價之後停損跑到成交價的錯邊時,那張單一開就會被停掉。
        這種情況不開倉才是對的。
        """
        result, insert = self._open(stoploss=100.0)

        self.assertFalse(result["success"])
        insert.assert_not_called()

    def test_leverage_that_puts_liquidation_inside_the_stop_is_rejected(self):
        """強平價比停損還近的倉位,實際上根本用不到停損。"""
        result, insert = self._open(stoploss=50.0, leverage=20.0)

        self.assertFalse(result["success"])
        self.assertIn("強平價", result["message"])
        insert.assert_not_called()

    def test_a_sane_combination_still_opens(self):
        """守門不能嚴到把正常的倉位也擋掉。"""
        result, insert = self._open(stoploss=97.0, leverage=3.0)

        self.assertTrue(result["success"])
        insert.assert_called_once()


class TestSchemaAndMapperStayInSync(unittest.TestCase):
    """
    Phase 0.5 抓到過一次:leverage 沒有被 SELECT 出來,於是每一筆交易
    不管實際槓桿多少都變成預設的 3x。同一類錯在 Phase 10 會更嚴重 ——
    漏掉 liquidation_price,強平就永遠不會觸發。
    """

    def _columns(self):
        return [
            line.strip().rstrip(",")
            for line in database_service.TRADE_COLUMNS.strip().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    def test_the_row_mapper_reads_every_selected_column(self):
        columns = self._columns()
        row = list(range(len(columns)))
        row[9] = row[10] = None          # opened_at / closed_at

        mapped = database_service._row_to_trade(row)

        # 索引用到最後一欄,代表沒有欄位被漏讀
        self.assertEqual(mapped["cost_basis"], len(columns) - 1)

    def test_liquidation_price_is_selected(self):
        """position_monitor 沒有這個欄位就判斷不出強平。"""
        self.assertIn("liquidation_price", self._columns())

    def test_reading_a_row_one_column_short_fails_loudly(self):
        columns = self._columns()
        short_row = list(range(len(columns) - 1))
        short_row[9] = short_row[10] = None

        with self.assertRaises(IndexError):
            database_service._row_to_trade(short_row)


class TestCostSummaryKeepsLegacyRowsSeparate(unittest.TestCase):

    def test_legacy_rows_are_counted_but_not_averaged_in(self):
        summary = paper_trading._cost_summary([
            {"cost_basis": "WITH_COSTS", "entry_fee": 1.0, "exit_fee": 1.0,
             "funding_usdt": 0.5, "gross_pnl_usdt": 100.0, "pnl_usdt": 97.5},
            {"cost_basis": "LEGACY_NO_COSTS", "pnl_usdt": 500.0},
        ])

        self.assertEqual(summary["trades_with_costs"], 1)
        self.assertEqual(summary["legacy_trades_without_costs"], 1)
        self.assertEqual(summary["net_pnl"], 97.5)
        self.assertEqual(summary["total_fees"], 2.0)

    def test_cost_drag_is_gross_minus_net(self):
        summary = paper_trading._cost_summary([
            {"cost_basis": "WITH_COSTS", "entry_fee": 2.0, "exit_fee": 2.0,
             "funding_usdt": 1.0, "gross_pnl_usdt": 100.0, "pnl_usdt": 95.0},
        ])

        self.assertAlmostEqual(summary["cost_drag"], 5.0, places=6)

    def test_an_empty_history_does_not_divide_by_zero(self):
        summary = paper_trading._cost_summary([])

        self.assertEqual(summary["trades_with_costs"], 0)
        self.assertEqual(summary["cost_drag"], 0)


if __name__ == "__main__":
    unittest.main()
