"""
開倉的強制檢查。

重點:沒有停損的倉位等於沒有風險上限,一律不允許開倉;
停損放在錯誤方向(做多的停損高於進場價)會在下一秒立刻停損出場,同樣要擋掉。
"""
import unittest
from unittest.mock import patch

import paper_trading
from database_service import DuplicateOpenTradeError


class TestCreatePaperTradeGuards(unittest.TestCase):

    def setUp(self):
        self.insert = patch.object(paper_trading, "insert_trade", return_value=1).start()
        patch.object(paper_trading, "logger").start()
        self.addCleanup(patch.stopall)

    def _create(self, **overrides):
        kwargs = {
            "symbol": "BTC/USDT",
            "entry_price": 100.0,
            "signal": "做多",
            "size_usdt": 1000,
            "stoploss": 97.0,
            "takeprofit": 106.0,
            "leverage": 3,
        }
        kwargs.update(overrides)
        return paper_trading.create_paper_trade(**kwargs)

    def test_rejects_missing_stop_loss(self):
        result = self._create(stoploss=None)
        self.assertFalse(result["success"])
        self.assertIn("停損", result["message"])
        self.insert.assert_not_called()

    def test_rejects_zero_stop_loss(self):
        self.assertFalse(self._create(stoploss=0)["success"])
        self.insert.assert_not_called()

    def test_rejects_long_stop_loss_above_entry(self):
        result = self._create(signal="做多", stoploss=103.0)
        self.assertFalse(result["success"])
        self.insert.assert_not_called()

    def test_rejects_short_stop_loss_below_entry(self):
        result = self._create(signal="做空", stoploss=97.0, takeprofit=94.0)
        self.assertFalse(result["success"])
        self.insert.assert_not_called()

    def test_rejects_take_profit_on_wrong_side(self):
        result = self._create(signal="做多", takeprofit=94.0)
        self.assertFalse(result["success"])
        self.insert.assert_not_called()

    def test_rejects_unknown_direction(self):
        self.assertFalse(self._create(signal="觀望")["success"])
        self.insert.assert_not_called()

    def test_rejects_non_positive_entry(self):
        self.assertFalse(self._create(entry_price=0)["success"])
        self.insert.assert_not_called()

    def test_rejects_non_positive_size(self):
        self.assertFalse(self._create(size_usdt=0)["success"])
        self.insert.assert_not_called()

    def test_accepts_valid_long(self):
        result = self._create()
        self.assertTrue(result["success"])
        self.insert.assert_called_once()
        self.assertEqual(result["trade"]["leverage"], 3.0)

    def test_accepts_valid_short(self):
        result = self._create(signal="做空", stoploss=103.0, takeprofit=94.0)
        self.assertTrue(result["success"])

    def test_take_profit_is_optional(self):
        self.assertTrue(self._create(takeprofit=None)["success"])

    def test_duplicate_open_is_reported_not_raised(self):
        self.insert.side_effect = DuplicateOpenTradeError("已有持倉")
        result = self._create()
        self.assertFalse(result["success"])
        self.assertIn("不重複開倉", result["message"])


class TestClosePaperTrade(unittest.TestCase):

    def setUp(self):
        patch.object(paper_trading, "logger").start()
        self.addCleanup(patch.stopall)

    def test_already_closed_is_flagged_and_does_not_touch_balance(self):
        with patch.object(paper_trading, "close_trade_atomic", return_value=None):
            result = paper_trading.close_paper_trade("BTC/USDT", 110)

        self.assertFalse(result["success"])
        self.assertTrue(result["already_closed"])

    def test_rejects_bad_exit_price(self):
        for bad in [None, 0, -1, "abc"]:
            self.assertFalse(paper_trading.close_paper_trade("BTC/USDT", bad)["success"])

    def test_successful_close_returns_trade_and_account(self):
        closed = {
            "symbol": "BTC/USDT", "signal": "做多", "pnl_pct": 50.0, "pnl_usdt": 500.0,
            "account": {"balance": 10500.0, "wins": 1, "losses": 0, "trades": 1},
        }
        with patch.object(paper_trading, "close_trade_atomic", return_value=dict(closed)):
            result = paper_trading.close_paper_trade("BTC/USDT", 110, "自動止盈")

        self.assertTrue(result["success"])
        self.assertEqual(result["account"]["balance"], 10500.0)
        self.assertEqual(result["trade"]["pnl_usdt"], 500.0)
        self.assertNotIn("account", result["trade"])
