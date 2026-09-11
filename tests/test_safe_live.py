"""
SAFE LIVE MODE(Master Prompt 第四十六節)。

第一次跑實單的時候,模擬盤驗證過的參數不該直接搬過來 —— 不是因為
參數算錯,而是模擬盤根本驗不到部分成交、掛單被拒、停損掛不上、
真實滑點,而那些會在實單的第一天就遇到。

這一組測試裡最重要的一條:**這一層只能收緊,不能放寬。**
一個「切到實單反而變寬鬆」的旋鈕,是這整個模組最不該有的東西。
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.config import settings
from agmcis.core.models import TradeIntent
from agmcis.risk.engine import AccountState, RiskEngine
from agmcis.safety import safe_live


def intent(**overrides):
    base = dict(symbol="BTC/USDT", market_type="perpetual", direction="做多",
                entry=65000.0, stop_loss=63050.0, take_profit=69000.0)
    base.update(overrides)
    return TradeIntent(**base)


def state(**overrides):
    base = dict(equity=10000.0, available_balance=10000.0, profit_factor=1.5)
    base.update(overrides)
    return AccountState(**base)


LIMITS = {
    "MAX_RISK_PER_TRADE_PCT": 1.0, "MAX_DRAWDOWN_PCT": 15, "MAX_EXPOSURE_PCT": 80,
    "MAX_OPEN_POSITIONS": 5, "MAX_LEVERAGE": 5, "MAX_DAILY_LOSS_USDT": 300,
    "MAX_TOTAL_OPEN_LOSS_USDT": -300, "MAX_CONSECUTIVE_LOSSES": 4,
    "MAX_TRADES_PER_DAY": 10, "MIN_PROFIT_FACTOR": 0.8,
    "MAX_WEEKLY_LOSS_USDT": 900, "AUTO_TRADING_ENABLED": True,
    "MAX_SYMBOL_EXPOSURE_PCT": 50, "MAX_CORRELATED_RISK_PCT": 3.0,
    "EMERGENCY_STOP_FILE": "/nonexistent/emergency.stop",
    "TRADING_PAUSE_FILE": "/nonexistent/trading_pause.flag",
}


class TestItOnlyAppliesInLiveMode(unittest.TestCase):

    def test_paper_mode_is_untouched(self):
        self.assertFalse(safe_live.is_active("paper"))
        self.assertEqual(safe_live.tighten("MAX_LEVERAGE", 5, mode="paper"), 5)
        self.assertIsNone(safe_live.max_notional(mode="paper"))

    def test_test_mode_is_untouched(self):
        """
        TEST 模式送的是真的 API 但不是真的錢。它有自己的風險,
        但不是「第一次用真錢」那一種,所以不套用實單額度。
        """
        self.assertFalse(safe_live.is_active("test"))

    def test_live_mode_is_active(self):
        self.assertTrue(safe_live.is_active("live"))

    def test_an_unknown_mode_is_not_treated_as_live(self):
        self.assertFalse(safe_live.is_active("nonsense"))

    def test_it_can_be_switched_off_but_defaults_on(self):
        with patch.object(settings, "SAFE_LIVE_MODE", False):
            self.assertFalse(safe_live.is_active("live"))

        self.assertTrue(safe_live.is_active("live"))


class TestItCanOnlyTighten(unittest.TestCase):
    """這一組是這個模組存在的理由。"""

    def test_a_stricter_live_limit_wins(self):
        with patch.object(settings, "MAX_LIVE_LEVERAGE", 2):
            self.assertEqual(safe_live.tighten("MAX_LEVERAGE", 5, mode="live"), 2)

    def test_a_looser_live_limit_is_ignored(self):
        """
        設定成「實單槓桿上限 10、一般上限 2」看起來像設定錯誤,
        但這裡不採用 10,也不報錯 —— 直接用 2。把實單限制寫成一個
        可以放寬一般限制的旋鈕,就是留了一個後門。
        """
        with patch.object(settings, "MAX_LIVE_LEVERAGE", 10):
            self.assertEqual(safe_live.tighten("MAX_LEVERAGE", 2, mode="live"), 2)

    def test_a_negative_limit_stays_negative_when_tightened(self):
        """
        MAX_TOTAL_OPEN_LOSS_USDT 之類的參數是負數。收緊它是
        「往 0 靠」,不是「變成正數」。
        """
        with patch.object(settings, "MAX_LIVE_DAILY_LOSS_USDT", 20):
            self.assertEqual(
                safe_live.tighten("MAX_DAILY_LOSS_USDT", -300, mode="live"), -20,
            )

    def test_a_positive_loss_limit_stays_positive(self):
        with patch.object(settings, "MAX_LIVE_DAILY_LOSS_USDT", 20):
            self.assertEqual(
                safe_live.tighten("MAX_DAILY_LOSS_USDT", 300, mode="live"), 20,
            )

    def test_a_parameter_with_no_live_counterpart_is_untouched(self):
        self.assertEqual(
            safe_live.tighten("MAX_DRAWDOWN_PCT", 15, mode="live"), 15,
        )

    def test_an_unset_live_limit_leaves_the_base_alone(self):
        with patch.object(settings, "MAX_LIVE_LEVERAGE", None):
            self.assertEqual(safe_live.tighten("MAX_LEVERAGE", 5, mode="live"), 5)

    def test_a_non_numeric_value_is_not_mangled(self):
        with patch.object(settings, "MAX_LIVE_LEVERAGE", "two"):
            self.assertEqual(safe_live.tighten("MAX_LEVERAGE", 5, mode="live"), 5)


class TestTheRiskEngineHonoursIt(unittest.TestCase):
    """
    設定得對不算數,要真的讓 Risk Engine 算出更小的倉位才算數。
    """

    def test_live_leverage_is_capped(self):
        paper = RiskEngine(limits=dict(LIMITS), mode="paper").evaluate(
            intent(), state(), atr=500, mtf_score=3,
        )
        live = RiskEngine(limits=dict(LIMITS), mode="live").evaluate(
            intent(), state(), atr=500, mtf_score=3,
        )

        self.assertTrue(paper.approved, paper.reason)
        self.assertTrue(live.approved, live.reason)
        self.assertLessEqual(live.leverage, settings.MAX_LIVE_LEVERAGE)
        self.assertLess(live.leverage, paper.leverage)

    def test_live_notional_is_capped(self):
        live = RiskEngine(limits=dict(LIMITS), mode="live").evaluate(
            intent(), state(), atr=500, mtf_score=3,
        )

        self.assertLessEqual(live.notional, settings.MAX_LIVE_POSITION_SIZE_USDT)

    def test_the_paper_notional_is_not_capped(self):
        """前提檢查:沒有這一條,上面那個測試通過也不代表上限有生效。"""
        paper = RiskEngine(limits=dict(LIMITS), mode="paper").evaluate(
            intent(), state(), atr=500, mtf_score=3,
        )

        self.assertGreater(paper.notional, settings.MAX_LIVE_POSITION_SIZE_USDT)

    def test_the_live_daily_loss_limit_blocks_much_earlier(self):
        losing = state(realized_pnl_24h=-25.0)

        self.assertTrue(
            RiskEngine(limits=dict(LIMITS), mode="paper").check_gate(losing).allowed,
            "一般模式下 -25 USDT 離 -300 還很遠",
        )

        gate = RiskEngine(limits=dict(LIMITS), mode="live").check_gate(losing)
        self.assertFalse(gate.allowed)
        self.assertIn("MAX_DAILY_LOSS", gate.blockers)

    def test_the_live_trade_count_limit_blocks_much_earlier(self):
        busy = state(trades_24h=4)

        self.assertTrue(
            RiskEngine(limits=dict(LIMITS), mode="paper").check_gate(busy).allowed,
        )

        gate = RiskEngine(limits=dict(LIMITS), mode="live").check_gate(busy)
        self.assertIn("MAX_TRADES_PER_DAY", gate.blockers)


class TestTheGateChecksIt(unittest.TestCase):

    def test_a_missing_live_limit_fails_the_gate(self):
        from agmcis.safety.live_gate import LiveGate

        with patch.object(settings, "MAX_LIVE_LEVERAGE", None):
            check = LiveGate().check_safe_live_limits()

        self.assertFalse(check.passed)
        self.assertIn("MAX_LIVE_LEVERAGE", check.detail)

    def test_switching_safe_live_off_fails_the_gate(self):
        from agmcis.safety.live_gate import LiveGate

        with patch.object(settings, "SAFE_LIVE_MODE", False):
            check = LiveGate().check_safe_live_limits()

        self.assertFalse(check.passed)

    def test_a_fully_configured_safe_live_passes_and_says_the_numbers(self):
        from agmcis.safety.live_gate import LiveGate

        check = LiveGate().check_safe_live_limits()

        self.assertTrue(check.passed, check.detail)
        self.assertIn(str(int(settings.MAX_LIVE_POSITION_SIZE_USDT)), check.detail)


class TestSnapshot(unittest.TestCase):

    def test_it_reports_what_is_actually_in_force(self):
        snapshot = safe_live.snapshot(mode="live")

        self.assertTrue(snapshot["active"])
        self.assertEqual(
            snapshot["effective"]["MAX_LEVERAGE"], settings.MAX_LIVE_LEVERAGE,
        )

    def test_paper_mode_reports_inactive_and_no_notional_cap(self):
        snapshot = safe_live.snapshot(mode="paper")

        self.assertFalse(snapshot["active"])
        self.assertIsNone(snapshot["max_notional_usdt"])
        self.assertEqual(
            snapshot["effective"]["MAX_LEVERAGE"], settings.MAX_LEVERAGE,
        )


if __name__ == "__main__":
    unittest.main()
