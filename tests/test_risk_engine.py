"""
Risk Engine、Position Sizing、槓桿、Kill Switch。

最重要的性質:
  1. 單筆風險金額由「權益 × 風險%」決定,**與槓桿無關**
  2. 停損越遠,倉位越小
  3. 槓桿由停損距離與波動度決定,**信心分數不會提高槓桿**
  4. decide() 產生的槓桿一定讓停損先於強平觸發
  5. 任何調整只會讓部位更保守,不會更寬鬆
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agmcis.core.models import TradeIntent
from agmcis.risk import leverage as lv
from agmcis.risk import position_sizing as ps
from agmcis.risk.engine import AccountState, RiskEngine
from agmcis.risk.kill_switch import KillSwitch


def intent(**overrides):
    base = dict(symbol="BTC/USDT", market_type="perpetual", direction="做多",
                entry=65000.0, stop_loss=63050.0, take_profit=69000.0)
    base.update(overrides)
    return TradeIntent(**base)


def healthy_state(**overrides):
    base = dict(equity=10000.0, available_balance=10000.0, open_positions=0,
                current_exposure_usdt=0.0, unrealized_pnl_usdt=0.0,
                realized_pnl_24h=0.0, trades_24h=0, consecutive_losses=0,
                max_drawdown_pct=0.0, profit_factor=1.5, open_symbols=[])
    base.update(overrides)
    return AccountState(**base)


LIMITS = {
    "MAX_RISK_PER_TRADE_PCT": 1.0, "MAX_DRAWDOWN_PCT": 15, "MAX_EXPOSURE_PCT": 80,
    "MAX_OPEN_POSITIONS": 5, "MAX_LEVERAGE": 5, "MAX_DAILY_LOSS_USDT": 300,
    "MAX_TOTAL_OPEN_LOSS_USDT": -300, "MAX_CONSECUTIVE_LOSSES": 4,
    "MAX_TRADES_PER_DAY": 10, "MIN_PROFIT_FACTOR": 0.8,
    "AUTO_TRADING_ENABLED": True,
    "EMERGENCY_STOP_FILE": "/nonexistent/emergency.stop",
    "TRADING_PAUSE_FILE": "/nonexistent/trading_pause.flag",
}


def engine():
    return RiskEngine(limits=dict(LIMITS))


class TestPositionSizingFundamentals(unittest.TestCase):

    def test_risk_amount_is_independent_of_leverage(self):
        """
        這是整個 sizing 的核心觀念。名目由風險與停損距離決定,
        槓桿只影響「這個名目要押多少保證金」。
        """
        risks = []
        for leverage in [1, 3, 5, 10, 20]:
            result = ps.calculate_size(
                equity=10000, stop_distance_pct=3.0,
                leverage=leverage, risk_per_trade_pct=1.0,
            )
            risks.append(round(result.risk_usdt, 6))
            self.assertAlmostEqual(result.notional, 3333.333333, places=4)

        self.assertEqual(len(set(risks)), 1, f"風險金額應該相同,實際 {risks}")
        self.assertAlmostEqual(risks[0], 100.0)

    def test_margin_shrinks_as_leverage_rises(self):
        low = ps.calculate_size(10000, 3.0, 1, 1.0)
        high = ps.calculate_size(10000, 3.0, 10, 1.0)
        self.assertLess(high.size_usdt, low.size_usdt)
        self.assertAlmostEqual(high.notional, low.notional, places=4)

    def test_wider_stop_means_smaller_position(self):
        tight = ps.calculate_size(10000, 1.0, 5, 1.0)
        wide = ps.calculate_size(10000, 10.0, 5, 1.0)

        self.assertGreater(tight.notional, wide.notional)
        self.assertAlmostEqual(tight.risk_usdt, wide.risk_usdt)

    def test_risk_pct_matches_configuration(self):
        result = ps.calculate_size(10000, 3.0, 5, 2.0)
        self.assertAlmostEqual(result.risk_pct_of_equity, 2.0, places=4)

    def test_no_stop_distance_is_rejected(self):
        for bad in [None, 0, -1]:
            result = ps.calculate_size(10000, bad, 5, 1.0)
            self.assertFalse(result.approved, bad)

    def test_exposure_cap_shrinks_notional(self):
        result = ps.calculate_size(
            10000, 0.5, 5, 1.0, max_exposure_pct=80, current_exposure_usdt=0,
        )
        self.assertEqual(result.notional, 8000.0)
        self.assertTrue(result.adjustments)

    def test_exposure_already_full_is_rejected(self):
        result = ps.calculate_size(
            10000, 3.0, 5, 1.0, max_exposure_pct=80, current_exposure_usdt=8000,
        )
        self.assertFalse(result.approved)

    def test_balance_cap_shrinks_margin(self):
        result = ps.calculate_size(10000, 3.0, 1, 1.0, available_balance=500)
        self.assertEqual(result.size_usdt, 500.0)
        self.assertTrue(result.adjustments)

    def test_below_min_notional_is_rejected_not_padded(self):
        """硬補到最小值會讓這一筆的風險超過設定上限。"""
        result = ps.calculate_size(100, 20.0, 1, 0.5, min_notional=5)
        self.assertFalse(result.approved)
        self.assertIn("不開", result.reason)

    def test_shrunk_position_reports_actual_reduced_risk(self):
        result = ps.calculate_size(
            10000, 0.5, 5, 1.0, max_exposure_pct=80, current_exposure_usdt=0,
        )
        self.assertLess(result.risk_usdt, 100.0)


class TestLeverageIsDrivenByRiskNotConfidence(unittest.TestCase):

    def test_decide_has_no_confidence_parameter(self):
        """信心是對方向的判斷,不是對能承受多少逆向波動的判斷。"""
        import inspect
        params = set(inspect.signature(lv.decide).parameters)
        self.assertNotIn("confidence", params)
        self.assertNotIn("score", params)

    def test_stop_distance_caps_leverage(self):
        self.assertAlmostEqual(lv.max_leverage_for_stop(3.0), 16.6667, places=3)
        self.assertAlmostEqual(lv.max_leverage_for_stop(10.0), 5.0)
        self.assertAlmostEqual(lv.max_leverage_for_stop(25.0), 2.0)

    def test_high_volatility_lowers_leverage(self):
        calm = lv.decide(3.0, max_leverage=5, atr=500, price=65000, mtf_score=3)
        wild = lv.decide(3.0, max_leverage=5, atr=3250, price=65000, mtf_score=3)
        self.assertGreater(calm.leverage, wild.leverage)
        self.assertEqual(wild.leverage, 1.0)

    def test_mixed_timeframes_lower_leverage(self):
        aligned = lv.decide(3.0, max_leverage=5, atr=500, price=65000, mtf_score=3)
        mixed = lv.decide(3.0, max_leverage=5, atr=500, price=65000, mtf_score=1)
        self.assertGreater(aligned.leverage, mixed.leverage)

    def test_missing_stop_forces_one_x(self):
        self.assertEqual(lv.decide(None, max_leverage=5).leverage, 1.0)

    def test_contract_limit_is_respected(self):
        decision = lv.decide(1.0, max_leverage=100, contract_max_leverage=20)
        self.assertLessEqual(decision.leverage, 20)

    def test_invariant_stop_always_triggers_before_liquidation(self):
        """
        最重要的不變量:decide() 產生的槓桿,一定讓停損先於強平觸發。
        這曾經因為 round() 進位而被打破 —— round(16.666, 2) = 16.67 高於真正上限。
        """
        violations = []
        for stop in [0.37, 0.5, 1, 2, 3, 4.13, 5, 7.5, 8.88, 10, 15, 25, 50]:
            for cap in [1, 3, 5, 10, 20, 125]:
                decision = lv.decide(stop, max_leverage=cap, contract_max_leverage=125)
                if not lv.stop_is_safer_than_liquidation(stop, decision.leverage):
                    violations.append((stop, cap, decision.leverage))

        self.assertEqual(violations, [], f"這些組合會在停損前被強平: {violations}")

    def test_leverage_never_below_one(self):
        self.assertGreaterEqual(lv.decide(90.0, max_leverage=5).leverage, 1.0)

    def test_reasons_explain_every_reduction(self):
        decision = lv.decide(3.0, max_leverage=5, atr=3250, price=65000, mtf_score=1)
        self.assertTrue(decision.reasons)


class TestRiskEngineGate(unittest.TestCase):

    def setUp(self):
        self.engine = engine()

    def _blocked_by(self, **state_overrides):
        return self.engine.check_gate(healthy_state(**state_overrides)).blockers

    def test_healthy_account_passes(self):
        self.assertTrue(self.engine.check_gate(healthy_state()).allowed)

    def test_drawdown_blocks_and_is_an_emergency(self):
        result = self.engine.check_gate(healthy_state(max_drawdown_pct=20))
        self.assertFalse(result.allowed)
        self.assertTrue(result.emergency)

    def test_daily_loss_blocks_and_is_an_emergency(self):
        result = self.engine.check_gate(healthy_state(realized_pnl_24h=-500))
        self.assertIn("MAX_DAILY_LOSS", result.blockers)
        self.assertTrue(result.emergency)

    def test_consecutive_losses_block(self):
        self.assertIn("MAX_CONSECUTIVE_LOSSES", self._blocked_by(consecutive_losses=4))

    def test_trades_per_day_blocks(self):
        self.assertIn("MAX_TRADES_PER_DAY", self._blocked_by(trades_24h=10))

    def test_exposure_blocks(self):
        self.assertIn("MAX_EXPOSURE", self._blocked_by(current_exposure_usdt=8500))

    def test_open_positions_block(self):
        self.assertIn("MAX_OPEN_POSITIONS", self._blocked_by(open_positions=5))

    def test_unrealized_loss_blocks(self):
        self.assertIn("MAX_TOTAL_OPEN_LOSS", self._blocked_by(unrealized_pnl_usdt=-400))

    def test_auto_trading_disabled_blocks(self):
        limits = dict(LIMITS, AUTO_TRADING_ENABLED=False)
        result = RiskEngine(limits=limits).check_gate(healthy_state())
        self.assertIn("AUTO_TRADING_DISABLED", result.blockers)

    def test_low_profit_factor_only_warns(self):
        result = self.engine.check_gate(healthy_state(profit_factor=0.5))
        self.assertTrue(result.allowed)
        self.assertTrue(result.warnings)

    def test_multiple_blockers_all_reported(self):
        blockers = self._blocked_by(
            max_drawdown_pct=20, consecutive_losses=9, trades_24h=99,
        )
        self.assertGreaterEqual(len(blockers), 3)


class TestRiskEngineEvaluate(unittest.TestCase):

    def setUp(self):
        self.engine = engine()

    def test_approves_and_sizes_a_good_intent(self):
        decision = self.engine.evaluate(intent(), healthy_state(), atr=500, mtf_score=3)

        self.assertTrue(decision.approved, decision.reason)
        self.assertIsNotNone(decision.size_usdt)
        self.assertIsNotNone(decision.leverage)
        self.assertAlmostEqual(decision.risk_usdt, 100.0, places=2)

    def test_gate_rejection_short_circuits_before_sizing(self):
        decision = self.engine.evaluate(
            intent(), healthy_state(max_drawdown_pct=20), atr=500,
        )
        self.assertFalse(decision.approved)
        self.assertIsNone(decision.size_usdt)
        self.assertIn("MAX_DRAWDOWN", decision.blockers)

    def test_duplicate_symbol_is_rejected(self):
        decision = self.engine.evaluate(
            intent(), healthy_state(open_symbols=["BTC/USDT"]),
        )
        self.assertFalse(decision.approved)
        self.assertIn("DUPLICATE_POSITION", decision.blockers)

    def test_high_volatility_reduces_leverage_not_risk(self):
        calm = self.engine.evaluate(intent(), healthy_state(), atr=500, mtf_score=3)
        wild = self.engine.evaluate(intent(), healthy_state(), atr=3250, mtf_score=3)

        self.assertLess(wild.leverage, calm.leverage)
        self.assertAlmostEqual(wild.risk_usdt, calm.risk_usdt, places=2)

    def test_min_notional_rejection_is_surfaced(self):
        decision = self.engine.evaluate(
            intent(), healthy_state(equity=10.0, available_balance=10.0),
            atr=500, min_notional=5000,
        )
        self.assertFalse(decision.approved)
        self.assertIn("SIZING_REJECTED", decision.blockers)

    def test_approved_decision_always_has_stop_before_liquidation(self):
        for stop_loss in [64350.0, 63050.0, 61750.0, 58500.0]:
            decision = self.engine.evaluate(
                intent(stop_loss=stop_loss), healthy_state(), atr=500, mtf_score=3,
            )
            if decision.approved:
                self.assertTrue(
                    lv.stop_is_safer_than_liquidation(
                        decision.intent.stop_distance_pct, decision.leverage
                    ),
                    f"stop_loss={stop_loss} lev={decision.leverage}",
                )


class TestKillSwitch(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stop_file = Path(self.tmp.name) / "emergency.stop"
        self.audit_file = Path(self.tmp.name) / "audit.log"

    def _switch(self, **kwargs):
        return KillSwitch(
            stop_file=self.stop_file, audit_file=self.audit_file, **kwargs
        )

    def test_arm_creates_the_flag_and_audits(self):
        switch = self._switch()
        result = switch.arm(triggered_by="risk_engine", reason="MAX_DAILY_LOSS")

        self.assertTrue(switch.is_armed)
        self.assertTrue(result.ok)
        records = switch.read_audit()
        self.assertEqual(records[-1]["event"], "ARM")
        self.assertEqual(records[-1]["triggered_by"], "risk_engine")

    def test_arm_does_not_touch_positions(self):
        """自動風控觸發時只該停新單,不該擅自平掉使用者的部位。"""
        closer = MagicMock()
        switch = self._switch(position_closer=closer,
                              open_positions_provider=lambda: [{"symbol": "BTC/USDT"}])
        switch.arm(reason="test")
        closer.assert_not_called()

    def test_disarm_is_also_audited(self):
        """誰在什麼時候把保險打開,同樣重要。"""
        switch = self._switch()
        switch.arm(reason="test")
        switch.disarm(triggered_by="brian", reason="已確認市場恢復")

        self.assertFalse(switch.is_armed)
        self.assertEqual(switch.read_audit()[-1]["event"], "DISARM")

    def test_panic_cancels_orders_and_closes_positions(self):
        canceller, closer = MagicMock(), MagicMock()
        switch = self._switch(
            order_canceller=canceller, position_closer=closer,
            open_orders_provider=lambda: [{"id": 1}, {"id": 2}],
            open_positions_provider=lambda: [{"symbol": "BTC/USDT"}],
        )
        result = switch.panic(triggered_by="brian", reason="系統異常")

        self.assertEqual(result.cancelled_orders, 2)
        self.assertEqual(result.closed_positions, 1)
        self.assertTrue(switch.is_armed)
        self.assertEqual(switch.read_audit()[-1]["event"], "PANIC")

    def test_panic_stops_new_orders_before_unwinding(self):
        """撤單與平倉期間絕對不能又開新倉。"""
        order = []
        switch = self._switch(
            order_canceller=lambda o: order.append(("cancel", self.stop_file.exists())),
            open_orders_provider=lambda: [{"id": 1}],
        )
        switch.panic(reason="test", close_positions=False)
        self.assertTrue(order[0][1], "撤單時停止旗標應該已經存在")

    def test_partial_failure_does_not_abort_the_rest(self):
        """緊急狀況下做到多少算多少,比一個失敗全放棄好。"""
        def flaky_cancel(order):
            if order["id"] == 1:
                raise RuntimeError("撤單失敗")

        closer = MagicMock()
        switch = self._switch(
            order_canceller=flaky_cancel, position_closer=closer,
            open_orders_provider=lambda: [{"id": 1}, {"id": 2}],
            open_positions_provider=lambda: [{"symbol": "BTC/USDT"}],
        )
        result = switch.panic(reason="test")

        self.assertEqual(result.cancelled_orders, 1)
        self.assertEqual(result.closed_positions, 1)
        self.assertTrue(result.errors)
        self.assertFalse(result.ok)

    def test_missing_capability_is_reported_not_silently_skipped(self):
        switch = self._switch()
        result = switch.panic(reason="test")

        self.assertTrue(any("撤單能力" in e for e in result.errors))
        self.assertTrue(any("平倉能力" in e for e in result.errors))
