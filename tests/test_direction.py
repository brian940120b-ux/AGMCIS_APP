"""
方向與停損方向驗證。

原本方向是中文字串散落在 12 個以上模組裡直接比對,打錯字就靜默變成 PnL = 0。
這裡把規則鎖住。
"""
import unittest

import direction


class TestDirection(unittest.TestCase):

    def test_accepts_chinese_and_english(self):
        for value in ["做多", "LONG", "buy", "BUY"]:
            self.assertTrue(direction.is_long(value), value)
        for value in ["做空", "SHORT", "sell", "SELL"]:
            self.assertTrue(direction.is_short(value), value)

    def test_wait_is_not_directional(self):
        for value in ["觀望", None, "", "HOLD", "🟡 Hold"]:
            self.assertFalse(direction.is_directional(value), value)

    def test_price_change_sign_by_direction(self):
        self.assertAlmostEqual(direction.price_change_pct("做多", 100, 110), 0.10)
        self.assertAlmostEqual(direction.price_change_pct("做多", 100, 90), -0.10)
        self.assertAlmostEqual(direction.price_change_pct("做空", 100, 90), 0.10)
        self.assertAlmostEqual(direction.price_change_pct("做空", 100, 110), -0.10)

    def test_price_change_rejects_bad_entry(self):
        with self.assertRaises(ValueError):
            direction.price_change_pct("做多", 0, 110)

    def test_price_change_rejects_unknown_direction(self):
        with self.assertRaises(ValueError):
            direction.price_change_pct("觀望", 100, 110)

    def test_stop_loss_must_be_on_correct_side(self):
        self.assertTrue(direction.stop_loss_is_valid("做多", 100, 97))
        self.assertFalse(direction.stop_loss_is_valid("做多", 100, 103))
        self.assertTrue(direction.stop_loss_is_valid("做空", 100, 103))
        self.assertFalse(direction.stop_loss_is_valid("做空", 100, 97))

    def test_missing_stop_loss_is_invalid(self):
        self.assertFalse(direction.stop_loss_is_valid("做多", 100, None))
        self.assertFalse(direction.stop_loss_is_valid("做多", 100, 0))

    def test_take_profit_must_be_on_correct_side(self):
        self.assertTrue(direction.take_profit_is_valid("做多", 100, 106))
        self.assertFalse(direction.take_profit_is_valid("做多", 100, 94))
        self.assertTrue(direction.take_profit_is_valid("做空", 100, 94))
        self.assertFalse(direction.take_profit_is_valid("做空", 100, 106))
