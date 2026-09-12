"""
策略健康度與退化偵測(Master Prompt 第七十三 ~ 七十六節)。

一個策略不會宣布自己壞掉。它會先變慢、再變差,然後某一天你回頭看,
才發現最近三十筆已經吃掉前面一百筆的利潤。

這組測試最在意的一條:**「近期比較差」不等於「漂移」。**
任何策略的任何三十筆都有大約一半機率比歷史平均差。用比大小當標準,
等於每兩次檢查就誤報一次,然後沒有人會再理這個警告。
"""
import json
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.core.enums import StrategyStatus
from agmcis.strategy import health

TMP = tempfile.mkdtemp(prefix="agmcis-test-health-")
_COUNTER = [0]


def store():
    _COUNTER[0] += 1
    tag = _COUNTER[0]
    return health.StatusStore(
        path=os.path.join(TMP, f"status-{tag}.json"),
        audit_path=os.path.join(TMP, f"audit-{tag}.log"),
    )


def trades(pnls, strategy="trend_following", regime=None):
    return [
        {"status": "CLOSED", "pnl_usdt": value,
         "strategy": strategy, "market_regime": regime}
        for value in pnls
    ]


def steady(n, value=10.0, seed=1, spread=5.0):
    rng = random.Random(seed)
    return [value + rng.uniform(-spread, spread) for _ in range(n)]


class TestTheStatusStoreFailsSafe(unittest.TestCase):
    """
    資料庫或檔案掛掉的時候,「所有策略看起來都是 LIVE」是錯誤的方向。
    """

    def test_an_unknown_strategy_defaults_to_paper(self):
        self.assertIs(store().get("never_seen"), health.DEFAULT_STATUS)
        self.assertIs(health.DEFAULT_STATUS, StrategyStatus.PAPER)

    def test_the_default_can_trade_on_paper_but_not_live(self):
        current = store()

        self.assertTrue(health.is_tradeable("x", mode="paper", store=current))
        self.assertFalse(health.is_tradeable("x", mode="live", store=current))

    def test_a_corrupt_file_does_not_promote_anything(self):
        current = store()
        current.path.parent.mkdir(parents=True, exist_ok=True)
        current.path.write_text("{ not json", encoding="utf-8")

        self.assertIs(current.get("anything"), health.DEFAULT_STATUS)
        self.assertFalse(health.is_tradeable("anything", mode="live", store=current))

    def test_a_file_that_is_not_an_object_is_rejected(self):
        current = store()
        current.path.parent.mkdir(parents=True, exist_ok=True)
        current.path.write_text("[1, 2, 3]", encoding="utf-8")

        self.assertIs(current.get("anything"), health.DEFAULT_STATUS)

    def test_a_paused_strategy_cannot_trade_anywhere(self):
        current = store()
        current.set("bad", StrategyStatus.PAUSED, reason="測試")

        self.assertFalse(health.is_tradeable("bad", mode="paper", store=current))
        self.assertFalse(health.is_tradeable("bad", mode="live", store=current))

    def test_only_approved_or_live_may_touch_real_money(self):
        current = store()
        current.set("researching", StrategyStatus.RESEARCH)
        current.set("ready", StrategyStatus.APPROVED)

        self.assertFalse(health.is_tradeable("researching", mode="live", store=current))
        self.assertTrue(health.is_tradeable("ready", mode="live", store=current))

    def test_every_change_is_audited(self):
        """策略被停掉而沒有人知道為什麼,跟沒有停掉一樣糟。"""
        current = store()
        current.set("x", StrategyStatus.PAUSED, reason="回撤 25%", actor="monitor")

        entries = current.read_audit()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["to"], "paused")
        self.assertIn("25%", entries[0]["reason"])
        self.assertEqual(entries[0]["actor"], "monitor")

    def test_an_unknown_status_is_rejected_rather_than_stored(self):
        with self.assertRaises(ValueError):
            store().set("x", "超級啟用")


class TestDriftIsAStatisticalTestNotAComparison(unittest.TestCase):

    def test_a_strategy_that_is_merely_unlucky_is_not_flagged(self):
        """
        同一個分佈抽出來的近期,不該被判成漂移 ——
        否則每兩次檢查就誤報一次,然後沒有人會再理這個警告。
        """
        values = steady(200, seed=7)
        check = health.detect_drift(values)

        self.assertFalse(check.drifted, check.detail)

    def test_a_genuinely_broken_strategy_is_flagged(self):
        values = steady(150, value=10.0, seed=3) + steady(30, value=-25.0, seed=4)
        check = health.detect_drift(values)

        self.assertTrue(check.drifted, check.detail)
        self.assertLess(check.sigma_gap, -health.DRIFT_SIGMA)

    def test_a_short_history_gives_no_verdict_rather_than_a_guess(self):
        check = health.detect_drift(steady(40))

        self.assertFalse(check.drifted)
        self.assertIn("不足以", check.detail)

    def test_a_strategy_that_improved_is_not_flagged(self):
        values = steady(150, value=2.0, seed=5) + steady(30, value=40.0, seed=6)
        check = health.detect_drift(values)

        self.assertFalse(check.drifted)
        self.assertGreater(check.sigma_gap, 0)

    def test_zero_variance_history_gives_no_verdict(self):
        """歷史完全沒有變異時算不出標準誤。回「不知道」,不回「沒漂移」。"""
        check = health.detect_drift([10.0] * 150 + [-50.0] * 30)

        self.assertFalse(check.drifted)
        self.assertIn("變異", check.detail)


class TestRegimeFit(unittest.TestCase):
    """
    順勢策略在趨勢市賺、在盤整市虧是正常的 —— 那不是策略壞掉。
    問題是市況已經轉成盤整,而系統還在用趨勢市的權重看它。
    """

    def _mixed(self):
        return (
            trades([12.0] * 20, regime="STRONG_BULL")
            + trades([-8.0] * 20, regime="RANGE")
        )

    def test_a_mismatched_regime_is_detected(self):
        fit = health.regime_fit(self._mixed(), current_regime="RANGE")

        self.assertTrue(fit.mismatch)
        self.assertEqual(fit.best_regime, "STRONG_BULL")

    def test_the_right_regime_is_not_flagged(self):
        fit = health.regime_fit(self._mixed(), current_regime="STRONG_BULL")

        self.assertFalse(fit.mismatch)

    def test_a_thin_regime_is_excluded_from_the_comparison(self):
        """三筆交易的「期望值」不是期望值。"""
        data = trades([12.0] * 20, regime="STRONG_BULL") + trades(
            [-50.0] * 3, regime="PANIC",
        )
        fit = health.regime_fit(data, current_regime="PANIC")

        self.assertFalse(fit.mismatch)
        self.assertIn("不足以判斷", fit.detail)
        self.assertEqual(fit.best_regime, "STRONG_BULL")

    def test_an_unknown_current_regime_gives_no_verdict(self):
        fit = health.regime_fit(self._mixed(), current_regime=None)

        self.assertFalse(fit.mismatch)
        self.assertIn("不知道目前市況", fit.detail)

    def test_trades_without_a_regime_are_skipped_not_bucketed_as_unknown(self):
        data = self._mixed() + trades([100.0] * 30, regime=None)
        fit = health.regime_fit(data, current_regime="RANGE")

        self.assertNotIn("None", fit.samples)
        self.assertEqual(fit.best_regime, "STRONG_BULL")


class TestTheStrategyKillSwitch(unittest.TestCase):

    def test_a_drawdown_breach_asks_to_pause(self):
        losing = trades([-40.0] * 20)
        report = health.evaluate("x", losing, store=store())

        self.assertTrue(report.should_pause)
        self.assertEqual(report.verdict, health.VERDICT_PAUSE)

    def test_apply_actually_pauses_it(self):
        current = store()
        report = health.evaluate("x", trades([-40.0] * 20), store=current)

        paused = health.apply([report], store=current)

        self.assertEqual(paused, ["x"])
        self.assertIs(current.get("x"), StrategyStatus.PAUSED)
        self.assertFalse(health.is_tradeable("x", store=current))

    def test_pausing_is_not_repeated_every_round(self):
        """否則每一輪都會寫一筆稽核,真正的變更就被淹沒了。"""
        current = store()
        report = health.evaluate("x", trades([-40.0] * 20), store=current)

        health.apply([report], store=current)
        again = health.apply([report], store=current)

        self.assertEqual(again, [])
        self.assertEqual(len(current.read_audit()), 1)

    def test_a_retired_strategy_is_left_alone(self):
        current = store()
        current.set("x", StrategyStatus.RETIRED, reason="人工退役")
        report = health.evaluate("x", trades([-40.0] * 20), store=current)

        self.assertEqual(health.apply([report], store=current), [])
        self.assertIs(current.get("x"), StrategyStatus.RETIRED)

    def test_drift_alone_does_not_pause(self):
        """
        漂移是「去看一下」,不是「已經確定壞了」。
        自動停一個只是運氣差的策略,會把系統停成一片空白。
        """
        values = steady(150, value=10.0, seed=11) + steady(30, value=-6.0, seed=12)
        report = health.evaluate("x", trades(values), store=store())

        self.assertEqual(report.verdict, health.VERDICT_DRIFT)
        self.assertFalse(report.should_pause)

    def test_evaluate_never_changes_state_by_itself(self):
        """Dashboard 要能看健康度而不產生副作用。"""
        current = store()
        health.evaluate("x", trades([-40.0] * 20), store=current)

        self.assertIs(current.get("x"), health.DEFAULT_STATUS)


class TestEvaluateAll(unittest.TestCase):

    def test_trades_are_grouped_by_strategy(self):
        data = (
            trades([10.0] * 40, strategy="a")
            + trades([-40.0] * 20, strategy="b")
        )
        reports = {r.name: r for r in health.evaluate_all(data, store=store())}

        self.assertEqual(sorted(reports), ["a", "b"])
        self.assertTrue(reports["b"].should_pause)
        self.assertFalse(reports["a"].should_pause)

    def test_trades_without_a_strategy_name_are_skipped(self):
        data = trades([10.0] * 40, strategy="a") + [
            {"status": "CLOSED", "pnl_usdt": 5.0},
        ]
        reports = health.evaluate_all(data, store=store())

        self.assertEqual([r.name for r in reports], ["a"])

    def test_the_monitor_reports_what_it_did(self):
        data = (
            trades([10.0] * 40, strategy="a")
            + trades([-40.0] * 20, strategy="b")
        )
        summary = health.run_drift_monitor(trades=data, store=store())

        self.assertEqual(summary["checked"], 2)
        self.assertEqual(summary["paused"], ["b"])


class TestTheRegistryHonoursTheStatus(unittest.TestCase):
    """
    一個被 PAUSE 的策略如果還在投票,那個 PAUSE 就只是一個標籤。
    """

    def _registry(self, current):
        from agmcis.strategy.registry import StrategyRegistry
        return StrategyRegistry(status_store=current, mode="paper")

    def test_a_paused_strategy_is_excluded_from_voting(self):
        current = store()
        registry = self._registry(current)
        name = registry.names[0]

        current.set(name, StrategyStatus.PAUSED, reason="測試")
        enabled, disabled = registry.active()

        self.assertIn(name, disabled)
        self.assertNotIn(name, [s.name for s in enabled])

    def test_live_mode_excludes_strategies_that_are_only_on_paper(self):
        from agmcis.strategy.registry import StrategyRegistry

        current = store()
        registry = StrategyRegistry(status_store=current, mode="live")
        enabled, disabled = registry.active()

        self.assertEqual(enabled, [])
        self.assertEqual(sorted(disabled), sorted(registry.names))

    def test_all_strategies_disabled_produces_a_clear_block_reason(self):
        from agmcis.strategy.registry import StrategyRegistry

        current = store()
        registry = StrategyRegistry(status_store=current, mode="live")

        consensus = registry.consensus(*_tradeable_context())

        self.assertFalse(consensus.is_actionable)
        self.assertEqual(consensus.blocked_reason, "沒有可用的策略")
        self.assertTrue(consensus.disabled)

    def test_the_disabled_list_is_reported_even_on_a_normal_round(self):
        current = store()
        registry = self._registry(current)
        name = registry.names[0]
        current.set(name, StrategyStatus.PAUSED, reason="測試")

        consensus = registry.consensus(*_tradeable_context())

        self.assertEqual(consensus.disabled, [name])
        self.assertNotIn(name, consensus.waiting)


def _tradeable_context():
    """一組資料合格、市況可交易的 indicators / regime。"""
    from agmcis.analysis.indicators import Indicators
    from agmcis.analysis.regime import MarketRegime, Regime, Volatility

    indicators = Indicators(
        symbol="BTC/USDT", timeframe="1h", price=100.0,
        ema20=101.0, ema50=100.0, ema60=99.0, rsi=55.0,
        macd=1.0, macd_signal=0.5, macd_hist=0.5, adx=30.0, atr=2.0,
        volume=1000.0, volume_ma20=800.0,
    )
    regime = MarketRegime(
        regime=Regime.BULL, volatility=Volatility.NORMAL,
    )
    return indicators, regime


if __name__ == "__main__":
    unittest.main()
