"""
分批平倉的帳務(Master Prompt 第五十七節)。

這裡有一個很容易寫錯、而且寫錯之後所有統計都會說謊的地方:

  **如果每一次分批都被算成一筆已平倉交易,勝率會衝到接近 100%。**

  你永遠先收 TP1(那一筆一定是賺的),而虧的部位還開著。
  那個數字不是勝率,是「分批停利第一階的達成率」——
  兩者長得一模一樣,意思完全不同。

所以分批不動 trades / wins / losses,只動 balance;
整筆交易要等最後一部分平掉才算一筆,總損益 = 分批 + 最後。
"""
import os
import sys
import unittest
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database_service


class FakeCursor:
    """回應 reduce_trade_atomic / close_trade_atomic 用到的查詢。"""

    def __init__(self, trade_row, account_row, insert_id=1):
        self._trade_row = trade_row
        self._account_row = account_row
        self._insert_id = insert_id
        self._next = None
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
        upper = " ".join(sql.split()).upper()

        if "FROM TRADES" in upper and "FOR UPDATE" in upper:
            self._next = self._trade_row
        elif upper.startswith("INSERT INTO TRADE_EXITS"):
            self._next = (self._insert_id,) if self._insert_id else None
        elif "FROM ACCOUNTS" in upper:
            self._next = self._account_row
        else:
            self._next = None

    def fetchone(self):
        return self._next

    def written(self, prefix):
        for sql, params in self.executed:
            if sql.upper().startswith(prefix):
                return params
        return None

    def all_written(self, prefix):
        return [p for sql, p in self.executed if sql.upper().startswith(prefix)]


@contextmanager
def fake_transaction(cursor):
    yield cursor


def open_row(trade_id=7, signal="做多", entry=100.0, size=1000.0, leverage=3.0,
             entry_fee=0.0, position_value=None, held_seconds=0.0,
             realized_partial=0.0, tp_stage=0, original=None):
    """reduce_trade_atomic 的 SELECT 欄位順序。"""
    if position_value is None:
        position_value = size * leverage
    if original is None:
        original = position_value
    return (trade_id, signal, entry, size, leverage, entry_fee,
            position_value, held_seconds, realized_partial, tp_stage, original)


def close_row(trade_id=7, signal="做多", entry=100.0, size=1000.0, leverage=3.0,
              entry_fee=0.0, position_value=None, held_seconds=0.0,
              realized_partial=0.0, original=None):
    """close_trade_atomic 的 SELECT 欄位順序。"""
    if position_value is None:
        position_value = size * leverage
    if original is None:
        original = position_value
    return (trade_id, signal, entry, size, leverage, entry_fee,
            position_value, held_seconds, realized_partial, original)


def reduce(cursor, fraction=0.3, exit_price=110.0, stage=1):
    with patch.object(database_service, "transaction",
                      lambda: fake_transaction(cursor)):
        return database_service.reduce_trade_atomic(
            "BTC/USDT", fraction, exit_price, stage, "TP1",
        )


def close(cursor, exit_price=110.0):
    with patch.object(database_service, "transaction",
                      lambda: fake_transaction(cursor)):
        return database_service.close_trade_atomic(
            "BTC/USDT", exit_price, "測試平倉",
        )


class TestThePartialDoesNotInflateTheWinRate(unittest.TestCase):
    """這一組是這整個功能最重要的部分。"""

    def test_a_partial_does_not_count_as_a_closed_trade(self):
        cursor = FakeCursor(open_row(), (10000.0, 0, 0, 0))
        reduce(cursor)

        balance, wins, losses, trades = cursor.written("UPDATE ACCOUNTS")[:4]

        self.assertEqual(trades, 0, "分批不是一筆交易")
        self.assertEqual(wins, 0, "分批不是一筆勝場")
        self.assertEqual(losses, 0)

    def test_a_partial_still_credits_the_balance(self):
        """不算一筆交易,但錢是真的進來了。"""
        cursor = FakeCursor(open_row(), (10000.0, 0, 0, 0))
        result = reduce(cursor)

        balance = cursor.written("UPDATE ACCOUNTS")[0]
        self.assertGreater(balance, 10000.0)
        self.assertAlmostEqual(balance, 10000.0 + result["pnl_usdt"], places=2)

    def test_the_position_stays_open(self):
        cursor = FakeCursor(open_row(), (10000.0, 0, 0, 0))
        reduce(cursor)

        trade_update = " ".join(
            sql for sql, _ in cursor.executed if sql.upper().startswith("UPDATE TRADES")
        )
        self.assertNotIn("CLOSED", trade_update.upper())

    def test_only_the_closed_fraction_is_realised(self):
        # 保證金 1000、3x、進場 100 出場 110 = +10% 價格
        # 全平的話是 1000 x 10% x 3 = 300;收 30% 就是 90
        cursor = FakeCursor(open_row(), (10000.0, 0, 0, 0))
        result = reduce(cursor, fraction=0.3)

        self.assertAlmostEqual(result["pnl_usdt"], 90.0, places=4)

    def test_the_remaining_position_shrinks_by_the_same_fraction(self):
        cursor = FakeCursor(open_row(size=1000.0, leverage=3.0),
                            (10000.0, 0, 0, 0))
        result = reduce(cursor, fraction=0.3)

        self.assertAlmostEqual(result["remaining_size_usdt"], 700.0, places=4)
        self.assertAlmostEqual(result["remaining_notional"], 2100.0, places=4)


class TestTheFinalCloseCountsTheWholeTrade(unittest.TestCase):

    def test_the_partial_pnl_is_folded_into_the_final_trade(self):
        """
        一筆「TP1 收了 +90、剩下的虧 -20」如果被記成 -20,
        它其實是一筆賺 70 的交易 —— 勝率與期望值都會被記反。
        """
        cursor = FakeCursor(
            close_row(size=700.0, position_value=2100.0,
                      realized_partial=90.0, original=3000.0),
            (10090.0, 0, 0, 0),
        )
        result = close(cursor, exit_price=99.0)

        self.assertGreater(result["pnl_usdt"], 0,
                           "含分批之後這是一筆賺錢的交易")

    def test_a_trade_that_is_still_a_loser_is_recorded_as_a_loss(self):
        cursor = FakeCursor(
            close_row(size=700.0, position_value=2100.0,
                      realized_partial=10.0, original=3000.0),
            (10010.0, 0, 0, 0),
        )
        close(cursor, exit_price=90.0)

        _, wins, losses, trades = cursor.written("UPDATE ACCOUNTS")[:4]
        self.assertEqual(wins, 0)
        self.assertEqual(losses, 1)
        self.assertEqual(trades, 1)

    def test_the_balance_only_gets_the_final_leg(self):
        """分批的部分在當時就入帳了,再加一次等於憑空生錢。"""
        cursor = FakeCursor(
            close_row(size=700.0, position_value=2100.0,
                      realized_partial=90.0, original=3000.0),
            (10090.0, 0, 0, 0),
        )
        result = close(cursor, exit_price=100.0)

        balance = cursor.written("UPDATE ACCOUNTS")[0]
        # 出場價等於進場價,最後一腿損益 ≈ 0,餘額不該再跳 +90
        self.assertAlmostEqual(balance, 10090.0, delta=1.0)
        self.assertAlmostEqual(result["pnl_usdt"], 90.0, delta=1.0)

    def test_roi_uses_the_original_margin_not_the_remainder(self):
        """
        用剩餘保證金當分母,分批收得越多 ROI 看起來越誇張 ——
        一筆收到只剩 10% 的交易可以算出好幾百趴。
        """
        cursor = FakeCursor(
            close_row(size=100.0, position_value=300.0,
                      realized_partial=90.0, original=3000.0),
            (10090.0, 0, 0, 0),
        )
        result = close(cursor, exit_price=100.0)

        # 原始保證金 1000,損益 90 -> 9%
        self.assertAlmostEqual(result["pnl_pct"], 9.0, delta=1.0)


class TestIdempotence(unittest.TestCase):
    """一次重試或一次排程重疊不該把 TP1 收兩次。"""

    def test_a_stage_that_was_already_taken_is_skipped(self):
        cursor = FakeCursor(open_row(tp_stage=1), (10000.0, 0, 0, 0))

        self.assertIsNone(reduce(cursor, stage=1))
        self.assertEqual(cursor.all_written("UPDATE ACCOUNTS"), [])

    def test_an_earlier_stage_is_also_skipped(self):
        cursor = FakeCursor(open_row(tp_stage=2), (10000.0, 0, 0, 0))

        self.assertIsNone(reduce(cursor, stage=1))

    def test_a_conflicting_insert_aborts_the_whole_leg(self):
        """
        唯一索引擋下來的時候,部位不能被縮、餘額不能被動 ——
        否則另一條路徑已經收過的那一份會被收第二次。
        """
        cursor = FakeCursor(open_row(), (10000.0, 0, 0, 0), insert_id=None)

        self.assertIsNone(reduce(cursor, stage=1))
        self.assertEqual(cursor.all_written("UPDATE TRADES"), [])
        self.assertEqual(cursor.all_written("UPDATE ACCOUNTS"), [])

    def test_no_open_position_is_not_an_error(self):
        cursor = FakeCursor(None, (10000.0, 0, 0, 0))
        self.assertIsNone(reduce(cursor))


class TestGuardrails(unittest.TestCase):

    def test_a_full_fraction_is_rejected(self):
        """要全平請用 close_trade_atomic —— 那條路徑才會結算整筆交易。"""
        cursor = FakeCursor(open_row(), (10000.0, 0, 0, 0))

        with self.assertRaises(ValueError):
            reduce(cursor, fraction=1.0)

    def test_a_zero_fraction_is_rejected(self):
        cursor = FakeCursor(open_row(), (10000.0, 0, 0, 0))

        with self.assertRaises(ValueError):
            reduce(cursor, fraction=0.0)

    def test_a_bad_exit_price_is_rejected(self):
        cursor = FakeCursor(open_row(), (10000.0, 0, 0, 0))

        with self.assertRaises(ValueError):
            reduce(cursor, exit_price=0)

    def test_an_unreadable_direction_is_rejected(self):
        cursor = FakeCursor(open_row(signal="???"), (10000.0, 0, 0, 0))

        with self.assertRaises(ValueError):
            reduce(cursor)

    def test_a_leg_cannot_lose_more_than_its_own_margin(self):
        cursor = FakeCursor(open_row(leverage=50.0), (10000.0, 0, 0, 0))
        result = reduce(cursor, fraction=0.3, exit_price=50.0)

        self.assertGreaterEqual(result["pnl_usdt"], -300.0)

    def test_the_entry_fee_is_split_across_legs(self):
        """
        全部算在第一腿會讓 TP1 看起來比實際差、TP3 比實際好 ——
        然後「哪一階的停利比較值得」這個問題會被答錯。
        """
        cursor = FakeCursor(open_row(entry_fee=30.0), (10000.0, 0, 0, 0))
        reduce(cursor, fraction=0.3)

        params = cursor.written("UPDATE TRADES")
        # (size, notional, entry_fee, stage, realized_partial, id)
        self.assertAlmostEqual(float(params[2]), 21.0, places=4)


if __name__ == "__main__":
    unittest.main()
