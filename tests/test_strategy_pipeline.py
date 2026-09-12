"""
統一訊號管線、策略層、市況判斷、評分。

Phase 0 稽核最根本的發現:系統有兩條互相矛盾的訊號管線,
Dashboard 顯示的訊號不是實際下單的依據。這裡鎖住合併後的行為。

另外舊策略**只做多**(sell_signal 是平多不是做空),
所以系統宣稱支援 Long/Short 但策略層根本產不出空單。
"""
import unittest
from unittest.mock import MagicMock, patch

from agmcis.analysis.indicators import Indicators
from agmcis.analysis.regime import MarketRegime, Regime, Volatility, detect
from agmcis.core.enums import Direction
from agmcis.signal import scorer
from agmcis.strategy.base import Strategy, StrategyVerdict
from agmcis.strategy.builtin import Breakout, MeanReversion, Momentum, TrendFollowing
from agmcis.strategy.registry import StrategyRegistry


def indicators(**overrides):
    base = dict(
        symbol="BTC/USDT", timeframe="1h", price=65000.0,
        ema20=66000.0, ema50=64000.0, ema60=64000.0,
        rsi=60.0, macd=10.0, macd_signal=5.0, macd_hist=5.0,
        adx=30.0, atr=650.0, bb_upper=67000.0, bb_lower=63000.0,
        volume=150.0, volume_ma20=100.0,
    )
    base.update(overrides)
    return Indicators(**base)


def bearish_indicators(**overrides):
    base = dict(
        ema20=64000.0, ema50=66000.0, ema60=66000.0,
        rsi=40.0, macd=-10.0, macd_signal=-5.0, macd_hist=-5.0,
    )
    base.update(overrides)
    return indicators(**base)


class TestIndicatorsAreOneImplementation(unittest.TestCase):
    """
    原本 technical_service 用 EMA60、strategy.analyze_symbol 用 EMA50,
    所以同一檔在兩條管線會得到不同的趨勢判斷。
    """

    def test_trend_uses_ema20_vs_ema50(self):
        self.assertEqual(indicators().trend, "BULLISH")
        self.assertEqual(bearish_indicators().trend, "BEARISH")

    def test_unknown_trend_when_data_is_bad(self):
        """UNKNOWN 代表不知道,不是中性。"""
        bad = indicators(data_ok=False, data_error="boom")
        self.assertEqual(bad.trend, "UNKNOWN")

    def test_derived_metrics(self):
        i = indicators(atr=650.0, price=65000.0, volume=150.0, volume_ma20=100.0)
        self.assertAlmostEqual(i.atr_pct, 1.0)
        self.assertAlmostEqual(i.volume_ratio, 1.5)
        self.assertTrue(i.has_trend_strength)
        self.assertFalse(i.is_ranging)


class TestMarketRegime(unittest.TestCase):

    def test_low_adx_is_range(self):
        self.assertIs(detect(indicators(adx=15)).regime, Regime.RANGE)

    def test_high_adx_bullish_is_strong_bull(self):
        self.assertIs(detect(indicators(adx=45)).regime, Regime.STRONG_BULL)

    def test_high_adx_bearish_is_strong_bear(self):
        self.assertIs(detect(bearish_indicators(adx=45)).regime, Regime.STRONG_BEAR)

    def test_extreme_volatility_is_not_tradeable(self):
        """極端波動下停損很容易被無意義地掃到。"""
        regime = detect(indicators(atr=4000.0, price=65000.0))
        self.assertIs(regime.volatility, Volatility.EXTREME)
        self.assertFalse(regime.is_tradeable)

    def test_bad_data_is_unknown_and_untradeable(self):
        regime = detect(indicators(data_ok=False, data_error="boom"))
        self.assertIs(regime.regime, Regime.UNKNOWN)
        self.assertFalse(regime.is_tradeable)

    def test_btc_reference_downgrades_a_counter_trend_signal(self):
        """個股看起來多頭但 BTC 在跌時,那個訊號要打折。"""
        alone = detect(indicators(adx=30))
        with_btc = detect(indicators(adx=30), btc_indicators=bearish_indicators())

        self.assertIs(alone.regime, Regime.BULL)
        self.assertIs(with_btc.regime, Regime.RANGE)

    def test_favours_matches_direction(self):
        bull = detect(indicators(adx=30))
        self.assertTrue(bull.favours("做多"))
        self.assertFalse(bull.favours("做空"))


class TestStrategiesSupportShorts(unittest.TestCase):
    """
    舊 strategies/*.py 全部只做多。這是整個系統宣稱支援 Long/Short
    卻產不出空單的根源。
    """

    def test_trend_following_produces_shorts(self):
        i = bearish_indicators()
        verdict = TrendFollowing().evaluate(i, detect(i))

        self.assertIs(verdict.direction, Direction.SHORT)
        self.assertGreater(verdict.stop_loss, i.price)
        self.assertLess(verdict.take_profit, i.price)

    def test_momentum_produces_shorts(self):
        i = bearish_indicators(macd_hist=-5.0, rsi=40.0)
        verdict = Momentum().evaluate(i, detect(i))
        self.assertIs(verdict.direction, Direction.SHORT)

    def test_breakout_produces_shorts(self):
        i = indicators(price=62000.0, bb_lower=63000.0, bb_upper=67000.0)
        verdict = Breakout().evaluate(i, detect(i))
        self.assertIs(verdict.direction, Direction.SHORT)

    def test_mean_reversion_produces_both_directions(self):
        ranging = dict(adx=15)
        oversold = indicators(rsi=25, price=62900.0, bb_lower=63000.0, **ranging)
        overbought = indicators(rsi=75, price=67100.0, bb_upper=67000.0, **ranging)

        self.assertIs(
            MeanReversion().evaluate(oversold, detect(oversold)).direction,
            Direction.LONG,
        )
        self.assertIs(
            MeanReversion().evaluate(overbought, detect(overbought)).direction,
            Direction.SHORT,
        )

    def test_every_builtin_strategy_can_emit_both_directions(self):
        import inspect
        from agmcis.strategy.builtin import BUILTIN_STRATEGIES

        for cls in BUILTIN_STRATEGIES:
            source = inspect.getsource(cls)
            self.assertIn("Direction.SHORT", source, cls.name)
            self.assertIn("Direction.LONG", source, cls.name)


class TestStrategyRegimeAwareness(unittest.TestCase):

    def test_trend_following_stays_out_of_ranges(self):
        """趨勢策略在盤整市會被反覆洗出場。"""
        i = indicators(adx=15)
        verdict = TrendFollowing().evaluate(i, detect(i))
        self.assertIs(verdict.direction, Direction.WAIT)

    def test_mean_reversion_only_trades_ranges(self):
        """單邊趨勢市裡超賣可以一路更超賣。"""
        trending = indicators(adx=45, rsi=25, price=62900.0, bb_lower=63000.0)
        verdict = MeanReversion().evaluate(trending, detect(trending))
        self.assertIs(verdict.direction, Direction.WAIT)


class TestConsensus(unittest.TestCase):

    def _registry(self, *verdicts):
        strategies = []
        for verdict in verdicts:
            strategy = MagicMock(spec=Strategy)
            strategy.name = verdict.strategy
            strategy.evaluate.return_value = verdict
            strategies.append(strategy)
        return StrategyRegistry(strategies=strategies, min_agreeing=2)

    def _verdict(self, name, direction, confidence=70, stop=64000, target=68000):
        return StrategyVerdict(
            strategy=name, direction=direction, confidence=confidence,
            stop_loss=stop, take_profit=target, reasons=[f"{name} 說話"],
        )

    def test_conflicting_strategies_mean_no_trade(self):
        """策略互相矛盾時不交易。這比多數決安全。"""
        registry = self._registry(
            self._verdict("a", Direction.LONG),
            self._verdict("b", Direction.LONG),
            self._verdict("c", Direction.SHORT, stop=66000, target=62000),
        )
        consensus = registry.consensus(indicators(), detect(indicators()))

        self.assertIs(consensus.direction, Direction.WAIT)
        self.assertEqual(consensus.blocked_reason, "策略互相矛盾")

    def test_not_enough_agreement_means_no_trade(self):
        registry = self._registry(
            self._verdict("a", Direction.LONG),
            StrategyVerdict(strategy="b", direction=Direction.WAIT),
        )
        consensus = registry.consensus(indicators(), detect(indicators()))

        self.assertIs(consensus.direction, Direction.WAIT)
        self.assertEqual(consensus.blocked_reason, "同向策略數不足")

    def test_agreement_produces_a_direction(self):
        registry = self._registry(
            self._verdict("a", Direction.LONG, 80),
            self._verdict("b", Direction.LONG, 60),
        )
        consensus = registry.consensus(indicators(), detect(indicators()))

        self.assertIs(consensus.direction, Direction.LONG)
        self.assertAlmostEqual(consensus.confidence, 70.0)

    def test_long_consensus_takes_the_most_conservative_stop(self):
        """不同策略對風險的評估不同時,聽最謹慎的那個。"""
        registry = self._registry(
            self._verdict("a", Direction.LONG, stop=63000, target=70000),
            self._verdict("b", Direction.LONG, stop=64500, target=67000),
        )
        consensus = registry.consensus(indicators(), detect(indicators()))

        self.assertEqual(consensus.stop_loss, 64500)   # 離進場最近
        self.assertEqual(consensus.take_profit, 67000)  # 最快到達

    def test_short_consensus_takes_the_most_conservative_stop(self):
        registry = self._registry(
            self._verdict("a", Direction.SHORT, stop=67000, target=60000),
            self._verdict("b", Direction.SHORT, stop=65500, target=63000),
        )
        consensus = registry.consensus(indicators(), detect(indicators()))

        self.assertEqual(consensus.stop_loss, 65500)
        self.assertEqual(consensus.take_profit, 63000)

    def test_bad_data_blocks_before_strategies_run(self):
        strategy = MagicMock(spec=Strategy)
        strategy.name = "a"
        registry = StrategyRegistry(strategies=[strategy])

        consensus = registry.consensus(
            indicators(data_ok=False, data_error="boom"), detect(indicators()),
        )
        self.assertIn("資料不可信", consensus.blocked_reason)
        strategy.evaluate.assert_not_called()

    def test_untradeable_regime_blocks_before_strategies_run(self):
        strategy = MagicMock(spec=Strategy)
        strategy.name = "a"
        registry = StrategyRegistry(strategies=[strategy])
        extreme = indicators(atr=4000.0)

        consensus = registry.consensus(extreme, detect(extreme))
        self.assertIn("市況不可交易", consensus.blocked_reason)
        strategy.evaluate.assert_not_called()

    def test_one_broken_strategy_does_not_break_the_round(self):
        good = MagicMock(spec=Strategy)
        good.name = "good"
        good.evaluate.return_value = self._verdict("good", Direction.LONG)

        broken = MagicMock(spec=Strategy)
        broken.name = "broken"
        broken.evaluate.side_effect = RuntimeError("boom")
        broken.wait.return_value = StrategyVerdict(
            strategy="broken", direction=Direction.WAIT
        )

        registry = StrategyRegistry(strategies=[good, broken], min_agreeing=1)
        consensus = registry.consensus(indicators(), detect(indicators()))

        self.assertIs(consensus.direction, Direction.LONG)


class TestScorer(unittest.TestCase):

    def _consensus(self, direction=Direction.LONG, confidence=80, agreeing=("a", "b")):
        from agmcis.strategy.registry import Consensus
        return Consensus(
            direction=direction, confidence=confidence, agreeing=list(agreeing),
        )

    def test_weights_sum_to_one_hundred(self):
        self.assertEqual(sum(scorer.WEIGHTS.values()), 100)

    def test_missing_data_scores_zero_not_half(self):
        """
        沒有資料不等於中性。舊系統遇到 None 會給 50 分的「中性」分數,
        讓系統分不出市場中性與資料壞掉。
        """
        self.assertEqual(scorer.MISSING, 0.0)

        bare = Indicators(symbol="X", timeframe="1h")
        breakdown = scorer.score(
            self._consensus(), bare, MarketRegime(), risk_reward=None,
        )
        self.assertIn("momentum", breakdown.missing)
        self.assertEqual(breakdown.components["momentum"], 0.0)

    def test_aligned_setup_scores_higher_than_misaligned(self):
        aligned = scorer.score(
            self._consensus(Direction.LONG), indicators(),
            detect(indicators()), risk_reward=3.0,
        )
        against = scorer.score(
            self._consensus(Direction.SHORT), indicators(),
            detect(indicators()), risk_reward=1.0,
        )
        self.assertGreater(aligned.total, against.total)

    def test_score_is_bounded(self):
        breakdown = scorer.score(
            self._consensus(confidence=100), indicators(),
            detect(indicators()), risk_reward=10.0,
        )
        self.assertLessEqual(breakdown.total, 100.0)
        self.assertGreaterEqual(breakdown.total, 0.0)

    def test_breakdown_explains_the_score(self):
        """Dashboard 要能回答「為什麼是 87 分」,不只顯示 87。"""
        breakdown = scorer.score(
            self._consensus(), indicators(), detect(indicators()), risk_reward=2.0,
        )
        self.assertEqual(set(breakdown.components), set(scorer.WEIGHTS))
        self.assertTrue(breakdown.notes)

    def test_coverage_reports_how_much_data_was_available(self):
        full = scorer.score(
            self._consensus(), indicators(), detect(indicators()), risk_reward=2.0,
        )
        sparse = scorer.score(
            self._consensus(), Indicators(symbol="X", timeframe="1h"),
            MarketRegime(), risk_reward=None,
        )
        self.assertGreater(full.coverage_pct, sparse.coverage_pct)


class TestTopOpportunities(unittest.TestCase):
    """Master Prompt 第 53 條:沒有足夠高品質的 setup 就不要硬選 TOP 3。"""

    def _signal(self, score, tradable=True):
        from agmcis.core.models import Signal
        return Signal(
            symbol=f"S{score}", market_type="perpetual",
            direction=Direction.LONG if tradable else Direction.WAIT,
            timeframe="1h", strategy="test", score=score,
            entry=100.0 if tradable else None,
            stop_loss=97.0 if tradable else None,
        )

    def test_returns_fewer_than_requested_when_quality_is_low(self):
        from agmcis.signal.pipeline import top_opportunities

        result = top_opportunities(
            [self._signal(90), self._signal(50), self._signal(40)],
            count=3, min_score=70,
        )
        self.assertEqual(len(result), 1)

    def test_returns_empty_when_nothing_qualifies(self):
        from agmcis.signal.pipeline import top_opportunities

        result = top_opportunities([self._signal(30)], count=3, min_score=70)
        self.assertEqual(result, [])

    def test_excludes_untradable_signals(self):
        from agmcis.signal.pipeline import top_opportunities

        result = top_opportunities(
            [self._signal(95, tradable=False)], count=3, min_score=70,
        )
        self.assertEqual(result, [])


class TestPipelineIsTheOnlySource(unittest.TestCase):
    """
    Dashboard 顯示的訊號,必須就是下單依據的訊號。
    兩個 shim 都要走同一條管線。
    """

    def test_scanner_service_delegates_to_the_pipeline(self):
        import scanner_service
        source = open(scanner_service.__file__, encoding="utf-8").read()
        self.assertIn("agmcis.signal.pipeline", source)

    def test_smart_ranking_delegates_to_the_pipeline(self):
        import smart_ranking
        source = open(smart_ranking.__file__, encoding="utf-8").read()
        self.assertIn("agmcis.signal.pipeline", source)

    def test_no_module_still_imports_the_old_second_scorer(self):
        """
        舊的第二套評分 strategy.analyze_symbol 不該再被任何活的模組使用。

        檢查 import 與呼叫,不是檢查字串是否出現 ——
        docstring 裡提到它是在解釋歷史,那不算使用。
        """
        import ast
        import pathlib

        offenders = []
        skip = {"strategy.py"}

        for path in pathlib.Path(".").glob("*.py"):
            if path.name in skip:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "strategy":
                    names = [a.name for a in node.names]
                    if "analyze_symbol" in names:
                        offenders.append(f"{path.name} imports analyze_symbol")

        self.assertEqual(offenders, [], f"仍在使用舊評分: {offenders}")

    def test_both_shims_report_the_same_direction_for_one_signal(self):
        from agmcis.core.models import Signal
        import scanner_service

        signal = Signal(
            symbol="BTC/USDT", market_type="perpetual", direction=Direction.SHORT,
            timeframe="1h", strategy="trend_following", score=85.0,
            entry=65000.0, stop_loss=66300.0, take_profit=61000.0,
        )
        with patch("scanner_service._scan", return_value=[signal]):
            row = scanner_service.scan_market(["BTC/USDT"])[0]

        self.assertEqual(row["action"], "SHORT")
        self.assertEqual(row["trade_signal"], "🔴 Strong Sell")
        self.assertEqual(row["stoploss"], 66300.0)


class TestPipelineCanActuallyTrade(unittest.TestCase):
    """
    迴歸測試:一個永遠不交易的管線跟壞掉沒兩樣。

    我第一版把 min_agreeing 設成 2,實測發現強趨勢時只有 trend_following
    出手,系統就永遠不交易 —— 而強趨勢正是順勢系統最該交易的情境。
    根因是「棄權被當成反對」:四個策略在找不同的 setup,
    breakout 沒出手不代表它反對趨勢。
    """

    def _series(self, direction="up"):
        import numpy as np

        rs = np.random.RandomState(7)
        base = np.concatenate([
            np.linspace(60000, 65000, 90),
            np.linspace(65000, 63500, 25),
            np.linspace(63500, 67500, 45),
        ])
        if direction == "down":
            base = 2 * 65000 - base
        return base + rs.normal(0, 80, len(base))

    def _analyse(self, close):
        import numpy as np
        import pandas as pd

        from agmcis.data import quality
        from agmcis.signal.pipeline import analyse_symbol

        n = len(close)
        volume = np.concatenate([np.full(120, 100.0), np.full(n - 120, 190.0)])
        df = pd.DataFrame({
            "timestamp": range(n), "open": close, "high": close * 1.004,
            "low": close * 0.996, "close": close, "volume": volume,
        })
        report = quality.QualityReport(symbol="X/USDT", timeframe="1h")

        with patch("agmcis.data.market_data.get_ohlcv_checked",
                   return_value=(df, report)), \
             patch("agmcis.data.market_data.get_price",
                   return_value=float(close[-1])):
            return analyse_symbol("X/USDT")

    def test_strong_uptrend_produces_a_tradable_long(self):
        signal = self._analyse(self._series("up"))

        self.assertTrue(signal.is_tradable, signal.reasons)
        self.assertIs(signal.direction, Direction.LONG)
        self.assertLess(signal.stop_loss, signal.entry)
        self.assertGreater(signal.take_profit, signal.entry)

    def test_strong_downtrend_produces_a_tradable_short(self):
        """舊系統的策略層根本產不出空單。"""
        signal = self._analyse(self._series("down"))

        self.assertTrue(signal.is_tradable, signal.reasons)
        self.assertIs(signal.direction, Direction.SHORT)
        self.assertGreater(signal.stop_loss, signal.entry)
        self.assertLess(signal.take_profit, signal.entry)

    def test_a_trend_signal_clears_the_quality_threshold(self):
        from agmcis.signal.pipeline import top_opportunities

        signal = self._analyse(self._series("up"))
        self.assertGreaterEqual(signal.score, 70)
        self.assertEqual(len(top_opportunities([signal], count=3, min_score=70)), 1)

    def test_abstention_is_not_counted_as_opposition(self):
        """
        只有少數策略同向時仍然可以交易,但分數會反映佐證不足 ——
        品質由 MIN_SIGNAL_SCORE 把關,不是由硬性的同向數門檻。

        不綁定「剛好一個策略同向」:第三十八節之後策略從四個變成九個,
        而這個測試的重點是「棄權不等於反對」,不是同向的確切數量。
        """
        signal = self._analyse(self._series("up"))
        breakdown = signal.score_breakdown
        consensus = signal.consensus

        self.assertGreaterEqual(len(consensus["agreeing"]), 1)
        self.assertEqual(consensus["opposing"], [], "沒有策略反對")
        self.assertTrue(consensus["waiting"], "有策略棄權")
        self.assertLess(
            len(consensus["agreeing"]) + len(consensus["disabled"]),
            len(consensus["agreeing"]) + len(consensus["waiting"])
            + len(consensus["disabled"]),
            "前提:這一輪確實有策略棄權",
        )
        self.assertTrue(signal.is_tradable)
        # 佐證不足,strategy_consensus 這一項拿不到滿分
        self.assertLess(
            breakdown["components"]["strategy_consensus"],
            breakdown["weights"]["strategy_consensus"],
        )

    def test_signal_converts_to_a_trade_intent(self):
        """管線的產出必須能餵進 Risk Engine。"""
        from agmcis.core.models import TradeIntent

        signal = self._analyse(self._series("up"))
        intent = TradeIntent.from_signal(signal)

        self.assertGreater(intent.stop_distance_pct, 0)
        self.assertIs(intent.direction, Direction.LONG)
