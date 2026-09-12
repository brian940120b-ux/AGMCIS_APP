"""
已實現損益必須乘上槓桿,而且與未實現損益用同一套算法。

這是 Phase 0.5 最重要的修正:
  原本 close_paper_trade 算 pnl_usdt = size_usdt * pnl_pct(不乘槓桿),
  而 portfolio_manager 算 upnl = size * roi(roi 已乘槓桿),
  同一筆倉位的浮動損益是已實現損益的 N 倍。帳戶餘額由前者累加,
  所以資金曲線、勝率、Profit Factor、最大回撤全部失真。
"""
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import database_service


class FakeCursor:
    """只回應 close_trade_atomic 用到的那幾個查詢,順序與真實流程一致。"""

    def __init__(self, trade_row, account_row):
        self._trade_row = trade_row
        self._account_row = account_row
        self._next = None
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
        upper = sql.upper()

        if "FROM TRADES" in upper and "FOR UPDATE" in upper:
            self._next = self._trade_row
        elif "FROM ACCOUNTS" in upper and "FOR UPDATE" not in upper:
            self._next = self._account_row
        elif "FROM ACCOUNTS" in upper and "FOR UPDATE" in upper:
            self._next = (1,)
        else:
            self._next = None

    def fetchone(self):
        return self._next

    def account_write(self):
        for sql, params in self.executed:
            if sql.upper().startswith("UPDATE ACCOUNTS"):
                return params
        return None

    def trade_write(self):
        for sql, params in self.executed:
            if sql.upper().startswith("UPDATE TRADES"):
                return params
        return None


@contextmanager
def fake_transaction(cursor):
    yield cursor


def trade_row(trade_id=7, signal="做多", entry=100.0, size=1000.0, leverage=3.0,
              entry_fee=0.0, position_value=None, held_seconds=0.0,
              realized_partial=0.0, original_position_value=None):
    """
    close_trade_atomic 的 SELECT 欄位順序。

    Phase 10 之後多了 entry_fee / position_value / 持倉秒數;
    分批停利(第五十七節)之後再多了已實現的分批損益與原始名目價值。

    這個 tuple 的長度必須跟 SQL 的欄位數完全一致 —— 少一個就會
    在解包時炸掉,那正是我們要的:SQL 改了而假資料沒改,
    測試應該紅,不應該悄悄驗到別的東西。
    """
    if position_value is None:
        position_value = size * leverage
    if original_position_value is None:
        original_position_value = position_value
    return (trade_id, signal, entry, size, leverage,
            entry_fee, position_value, held_seconds,
            realized_partial, original_position_value)


class TestRealizedPnlUsesLeverage(unittest.TestCase):

    def _close(self, signal, entry, exit_price, size, leverage, balance=10000.0):
        cursor = FakeCursor(
            trade_row=trade_row(signal=signal, entry=entry, size=size,
                                leverage=leverage),
            account_row=(balance, 0, 0, 0),
        )
        with patch.object(database_service, "transaction", lambda: fake_transaction(cursor)):
            result = database_service.close_trade_atomic("BTC/USDT", exit_price, "測試平倉")
        return result, cursor

    def test_long_profit_scales_with_leverage(self):
        # 進場 100 出場 110 = 價格 +10%;5x 槓桿、保證金 1000 USDT
        # 名目 = 1000 x 5 = 5000,損益 = 5000 x 10% = 500 USDT,ROI = 50%
        result, _ = self._close("做多", 100, 110, 1000, 5)
        self.assertAlmostEqual(result["pnl_usdt"], 500.0)
        self.assertAlmostEqual(result["pnl_pct"], 50.0)

    def test_unleveraged_case_is_unchanged(self):
        result, _ = self._close("做多", 100, 110, 1000, 1)
        self.assertAlmostEqual(result["pnl_usdt"], 100.0)
        self.assertAlmostEqual(result["pnl_pct"], 10.0)

    def test_short_profit_when_price_falls(self):
        result, _ = self._close("做空", 100, 90, 1000, 3)
        self.assertAlmostEqual(result["pnl_usdt"], 300.0)
        self.assertAlmostEqual(result["pnl_pct"], 30.0)

    def test_short_loses_when_price_rises(self):
        result, _ = self._close("做空", 100, 110, 1000, 3)
        self.assertAlmostEqual(result["pnl_usdt"], -300.0)

    def test_pnl_usdt_equals_size_times_roi(self):
        """內部一致性:pnl_usdt 必須等於 size_usdt x pnl_pct / 100。"""
        result, _ = self._close("做多", 250, 262.5, 800, 4)
        self.assertAlmostEqual(
            result["pnl_usdt"], result["size_usdt"] * result["pnl_pct"] / 100, places=4,
        )

    def test_balance_and_counters_updated_in_same_transaction(self):
        result, cursor = self._close("做多", 100, 110, 1000, 5, balance=10000.0)
        balance, wins, losses, trades, _id = cursor.account_write()
        self.assertAlmostEqual(balance, 10500.0)
        self.assertEqual((wins, losses, trades), (1, 0, 1))
        self.assertEqual(result["account"]["balance"], 10500.0)

    def test_loss_increments_losses(self):
        _, cursor = self._close("做多", 100, 95, 1000, 2)
        _, wins, losses, trades, _id = cursor.account_write()
        self.assertEqual((wins, losses, trades), (0, 1, 1))

    def test_marks_new_rows_as_leveraged_basis(self):
        """歷史資料保留原值並標記 LEGACY_UNLEVERAGED;新資料標記 LEVERAGED。"""
        result, cursor = self._close("做多", 100, 110, 1000, 5)
        self.assertEqual(result["pnl_basis"], "LEVERAGED")

        trade_update = next(
            sql for sql, _ in cursor.executed if sql.upper().startswith("UPDATE TRADES")
        )
        self.assertIn("pnl_basis = 'LEVERAGED'", trade_update)

    def test_returns_none_when_no_open_trade(self):
        """另一條路徑已經平掉時回傳 None,呼叫端就不會重複調整餘額。"""
        cursor = FakeCursor(trade_row=None, account_row=(10000.0, 0, 0, 0))
        with patch.object(database_service, "transaction", lambda: fake_transaction(cursor)):
            result = database_service.close_trade_atomic("BTC/USDT", 110, "測試")
        self.assertIsNone(result)
        self.assertIsNone(cursor.account_write())

    def test_rejects_zero_entry_price(self):
        cursor = FakeCursor(trade_row=trade_row(entry=0), account_row=(10000.0, 0, 0, 0))
        with patch.object(database_service, "transaction", lambda: fake_transaction(cursor)):
            with self.assertRaises(ValueError):
                database_service.close_trade_atomic("BTC/USDT", 110, "測試")

    def test_rejects_unknown_direction(self):
        cursor = FakeCursor(trade_row=trade_row(signal="觀望"),
                            account_row=(10000.0, 0, 0, 0))
        with patch.object(database_service, "transaction", lambda: fake_transaction(cursor)):
            with self.assertRaises(ValueError):
                database_service.close_trade_atomic("BTC/USDT", 110, "測試")
