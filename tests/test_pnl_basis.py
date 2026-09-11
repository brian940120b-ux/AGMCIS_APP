"""
損益基準分離。

Phase 0.5 之前的已平倉資料損益漏乘槓桿。那些數字保留原值不改寫 ——
改寫歷史財務紀錄不可逆。但也不能讓兩種基準混在同一個勝率裡:
一筆 5x 的交易在舊基準下損益只有實際的五分之一,混著算兩邊都不是。
"""
import unittest
from unittest.mock import patch

import analytics


def trade(pnl, basis="LEVERAGED", symbol="BTC/USDT"):
    return {
        "symbol": symbol, "status": "CLOSED", "signal": "做多",
        "pnl_usdt": pnl, "pnl_pct": pnl / 10, "pnl_basis": basis,
        "entry_price": 100.0, "exit_price": 110.0, "size_usdt": 1000.0,
        "leverage": 5.0,
    }


class TestSplitByBasis(unittest.TestCase):

    def test_splits_on_basis(self):
        comparable, legacy = analytics.split_by_pnl_basis([
            trade(100, "LEVERAGED"),
            trade(-50, "LEGACY_UNLEVERAGED"),
            trade(20, "LEVERAGED"),
        ])
        self.assertEqual(len(comparable), 2)
        self.assertEqual(len(legacy), 1)

    def test_missing_basis_counts_as_legacy(self):
        """未標記的資料保守假設為舊基準。"""
        comparable, legacy = analytics.split_by_pnl_basis([
            {"pnl_usdt": 10},
            {"pnl_usdt": 10, "pnl_basis": None},
        ])
        self.assertEqual(comparable, [])
        self.assertEqual(len(legacy), 2)


class TestAnalyticsExcludesLegacyByDefault(unittest.TestCase):

    def _run(self, trades, **kwargs):
        with patch.object(analytics, "load_trades", return_value=trades), \
             patch.object(analytics, "load_account", return_value={"balance": 10000}):
            return analytics.get_trade_analytics(**kwargs)

    def test_legacy_trades_do_not_pollute_win_rate(self):
        """兩筆新資料全贏,舊資料全輸。預設勝率應該是 100%,不是 50%。"""
        result = self._run([
            trade(100, "LEVERAGED"),
            trade(200, "LEVERAGED"),
            trade(-500, "LEGACY_UNLEVERAGED"),
            trade(-300, "LEGACY_UNLEVERAGED"),
        ])
        self.assertEqual(result["total_trades"], 2)
        self.assertEqual(result["win_rate"], 100.0)

    def test_legacy_trades_are_still_reported(self):
        """排除不等於隱藏 —— 使用者要看得到那些交易還在。"""
        result = self._run([
            trade(100, "LEVERAGED"),
            trade(-500, "LEGACY_UNLEVERAGED"),
        ])
        self.assertEqual(result["legacy"]["count"], 1)
        self.assertEqual(result["legacy"]["total_pnl"], -500)
        self.assertFalse(result["legacy"]["comparable"])
        self.assertIn("note", result["legacy"])

    def test_basis_is_labelled(self):
        result = self._run([trade(100, "LEVERAGED")])
        self.assertEqual(result["pnl_basis"], "LEVERAGED")

    def test_include_legacy_is_opt_in_and_labelled_mixed(self):
        result = self._run([
            trade(100, "LEVERAGED"),
            trade(-500, "LEGACY_UNLEVERAGED"),
        ], include_legacy=True)

        self.assertEqual(result["total_trades"], 2)
        self.assertEqual(result["pnl_basis"], "MIXED")

    def test_only_legacy_data_yields_empty_main_stats_not_a_crash(self):
        result = self._run([trade(-500, "LEGACY_UNLEVERAGED")])

        self.assertEqual(result["total_trades"], 0)
        self.assertEqual(result["win_rate"], 0)
        self.assertEqual(result["legacy"]["count"], 1)

    def test_profit_factor_uses_comparable_trades_only(self):
        result = self._run([
            trade(100, "LEVERAGED"),
            trade(-50, "LEVERAGED"),
            trade(-9999, "LEGACY_UNLEVERAGED"),
        ])
        self.assertEqual(result["profit_factor"], 2.0)

    def test_equity_curve_excludes_legacy(self):
        result = self._run([
            trade(100, "LEVERAGED"),
            trade(-9999, "LEGACY_UNLEVERAGED"),
        ])
        self.assertEqual(result["equity_curve"][-1], analytics.START_BALANCE + 100)
