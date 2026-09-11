"""
Strategy Lab。

這一層的價值完全在於**它會不會拒絕**。一個只會說「通過」的驗證層
比沒有驗證層更危險,因為它給的是虛假的信心。

所以測試的重點不是「好策略會通過」,而是:
  * 過擬合的策略會被抓出來
  * 樣本太小不會被當成證據
  * OOS 資料不會被用來挑參數
  * 失敗的組合不會被靜默吃掉變成 0%
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.backtest import metrics as metrics_module
from agmcis.backtest.costs import ZERO_COSTS
from agmcis.backtest.engine import BacktestEngine
from agmcis.lab import ensemble, evaluate, montecarlo, runner, scoring, splits


def candle(i, open_, high, low, close, volume=100.0):
    return {"time": i * 3_600_000, "open": open_, "high": high,
            "low": low, "close": close, "volume": volume}


def wave_candles(count=900, base=100.0):
    rows = []
    for i in range(count):
        close = base + 10.0 * math.sin(i / 9.0) + 3.0 * math.sin(i / 2.5)
        open_ = base + 10.0 * math.sin((i - 1) / 9.0) + 3.0 * math.sin((i - 1) / 2.5)
        rows.append(candle(i, open_, max(open_, close) + 0.5,
                           min(open_, close) - 0.5, close))
    return rows


def build_dip_buyer(candles, params):
    """收盤比 N 根前低就做多,停損放在 stop_pct 之外。"""
    lookback = int(params.get("lookback", 5))
    stop_pct = float(params.get("stop_pct", 3.0))

    def signal_fn(history, index):
        if index < lookback:
            return None
        now = history[index]["close"]
        before = history[index - lookback]["close"]
        if now >= before:
            return None
        return {
            "direction": "做多",
            "stop_loss": now * (1 - stop_pct / 100),
            "take_profit": now * (1 + stop_pct / 100),
        }

    return signal_fn


class TestSplitsAreTimeOrdered(unittest.TestCase):
    """隨機打散再切 train/test 是金融資料最典型的洩漏。"""

    def test_oos_always_comes_after_the_training_data(self):
        split = splits.in_sample_out_of_sample(1000, 0.3)

        self.assertEqual(split.train_end, split.test_start)
        self.assertGreater(split.test_start, split.train_start)

    def test_walk_forward_test_windows_never_overlap(self):
        windows = splits.walk_forward(1000, 300, 100)

        for earlier, later in zip(windows, windows[1:]):
            self.assertGreaterEqual(later.test_start, earlier.test_end)

    def test_walk_forward_test_window_immediately_follows_its_own_training(self):
        for window in splits.walk_forward(1000, 300, 100):
            self.assertEqual(window.test_start, window.train_end)

    def test_anchored_mode_keeps_the_start_and_grows(self):
        windows = splits.walk_forward(1000, 300, 100, anchored=True)

        self.assertTrue(all(w.train_start == 0 for w in windows))
        sizes = [w.train_size for w in windows]
        self.assertEqual(sizes, sorted(sizes))

    def test_not_enough_data_raises_instead_of_returning_nothing(self):
        """靜默回傳空 list 會讓呼叫端以為「驗證過了,只是沒有視窗」。"""
        with self.assertRaises(ValueError):
            splits.walk_forward(100, 300, 100)


class TestObjectiveIsNotTotalReturn(unittest.TestCase):

    def _metrics(self, **kwargs):
        stats = metrics_module.Metrics()
        for key, value in kwargs.items():
            setattr(stats, key, value)
        return stats

    def test_negative_expectancy_scores_negative_no_matter_the_return(self):
        stats = self._metrics(total_trades=200, expectancy_r=-0.1,
                              total_return_pct=500.0, max_drawdown_pct=5.0)

        self.assertLess(scoring.objective(stats), 0)

    def test_small_samples_score_lower_than_large_ones(self):
        few = self._metrics(total_trades=5, expectancy_r=0.2, max_drawdown_pct=10.0)
        many = self._metrics(total_trades=200, expectancy_r=0.2, max_drawdown_pct=10.0)

        self.assertLess(scoring.objective(few), scoring.objective(many))

    def test_bigger_drawdown_scores_lower(self):
        shallow = self._metrics(total_trades=100, expectancy_r=0.2,
                                max_drawdown_pct=5.0)
        deep = self._metrics(total_trades=100, expectancy_r=0.2,
                             max_drawdown_pct=50.0)

        self.assertLess(scoring.objective(deep), scoring.objective(shallow))

    def test_no_trades_is_not_comparable(self):
        self.assertIsNone(scoring.objective(self._metrics(total_trades=0)))


class TestHealthScoreRejects(unittest.TestCase):

    def _metrics(self, **kwargs):
        stats = metrics_module.Metrics()
        stats.total_trades = kwargs.pop("total_trades", 100)
        stats.expectancy_r = kwargs.pop("expectancy_r", 0.2)
        stats.profit_factor = kwargs.pop("profit_factor", 1.5)
        stats.max_drawdown_pct = kwargs.pop("max_drawdown_pct", 10.0)
        for key, value in kwargs.items():
            setattr(stats, key, value)
        return stats

    def test_too_few_trades_is_a_blocker_however_good_the_numbers(self):
        health = scoring.health_score(
            self._metrics(total_trades=8, expectancy_r=2.0, profit_factor=9.0,
                          max_drawdown_pct=1.0),
        )

        self.assertEqual(health.verdict, "REJECT")
        self.assertTrue(any("筆交易" in b for b in health.blockers))

    def test_negative_expectancy_is_a_blocker(self):
        health = scoring.health_score(self._metrics(expectancy_r=-0.05))

        self.assertEqual(health.verdict, "REJECT")

    def test_severe_degradation_from_is_to_oos_is_a_blocker(self):
        """IS 很好、OOS 幾乎沒了 —— 這就是過擬合的樣子。"""
        health = scoring.health_score(
            oos_metrics=self._metrics(expectancy_r=0.02),
            is_metrics=self._metrics(expectancy_r=0.50),
        )

        self.assertEqual(health.verdict, "REJECT")
        self.assertTrue(any("過擬合" in b for b in health.blockers))

    def test_liquidation_in_oos_is_a_blocker(self):
        health = scoring.health_score(self._metrics(liquidations=1))

        self.assertEqual(health.verdict, "REJECT")

    def test_profit_factor_is_not_treated_as_infinite_when_there_are_no_losses(self):
        health = scoring.health_score(self._metrics(profit_factor=None))

        self.assertEqual(health.components["ProfitFactor"], 0.0)
        self.assertTrue(any("無法評估" in w for w in health.warnings))

    def test_a_blocked_result_scores_zero_however_good_the_components(self):
        """
        「REJECT 但 85 分」這種組合會被拿去凹。blocker 成立就是 0 分,
        分項合計留在 raw_score 供診斷。
        """
        health = scoring.health_score(
            self._metrics(total_trades=6, expectancy_r=2.6, profit_factor=8.0,
                          max_drawdown_pct=2.0),
        )

        self.assertEqual(health.verdict, "REJECT")
        self.assertEqual(health.score, 0.0)
        self.assertGreater(health.raw_score, 50)

    def test_a_genuinely_solid_result_can_pass(self):
        """驗證層也不能是永遠說 NO —— 那樣同樣沒有資訊。"""
        health = scoring.health_score(
            oos_metrics=self._metrics(total_trades=120, expectancy_r=0.30,
                                      profit_factor=1.9, max_drawdown_pct=8.0),
            is_metrics=self._metrics(total_trades=300, expectancy_r=0.33),
        )

        self.assertEqual(health.verdict, "PASS")
        self.assertEqual(health.blockers, [])


class TestDegradation(unittest.TestCase):

    def _metrics(self, expectancy):
        stats = metrics_module.Metrics()
        stats.total_trades = 100
        stats.expectancy_r = expectancy
        return stats

    def test_zero_when_oos_matches_is(self):
        value = scoring.expectancy_degradation(
            self._metrics(0.2), self._metrics(0.2),
        )
        self.assertAlmostEqual(value, 0.0, places=6)

    def test_above_one_when_oos_turns_negative(self):
        value = scoring.expectancy_degradation(
            self._metrics(0.2), self._metrics(-0.2),
        )
        self.assertGreater(value, 1.0)

    def test_none_when_is_had_no_edge_to_begin_with(self):
        self.assertIsNone(
            scoring.expectancy_degradation(self._metrics(-0.1), self._metrics(0.1)),
        )


class TestMonteCarlo(unittest.TestCase):

    class FakeTrade:
        def __init__(self, r):
            self.r_multiple = r
            self.is_open = False

    class FakeResult:
        def __init__(self, rs):
            self.trades = [TestMonteCarlo.FakeTrade(r) for r in rs]

        @property
        def closed_trades(self):
            return self.trades

    def test_a_losing_edge_rarely_ends_profitable(self):
        result = self.FakeResult([-1.0] * 60 + [1.0] * 40)
        output = montecarlo.run(result, runs=300, risk_pct=1.0, seed=7)

        self.assertLess(output.probability_of_profit, 0.2)

    def test_a_winning_edge_usually_ends_profitable(self):
        result = self.FakeResult([-1.0] * 40 + [2.0] * 60)
        output = montecarlo.run(result, runs=300, risk_pct=1.0, seed=7)

        self.assertGreater(output.probability_of_profit, 0.8)

    def test_higher_risk_per_trade_raises_risk_of_ruin(self):
        result = self.FakeResult([-1.0] * 50 + [1.5] * 50)

        small = montecarlo.run(result, runs=400, risk_pct=1.0, seed=3)
        large = montecarlo.run(result, runs=400, risk_pct=25.0, seed=3)

        self.assertGreaterEqual(large.risk_of_ruin, small.risk_of_ruin)

    def test_small_samples_are_flagged(self):
        output = montecarlo.run(self.FakeResult([1.0, -1.0, 1.0]), runs=100, seed=1)

        self.assertTrue(any("樣本太小" in w for w in output.warnings))

    def test_no_trades_produces_no_fake_confidence(self):
        output = montecarlo.run(self.FakeResult([]), runs=100)

        self.assertEqual(output.runs, 0)
        self.assertEqual(output.probability_of_profit, 0.0)
        self.assertTrue(output.warnings)

    def test_shuffle_mode_uses_exactly_the_same_trades_each_run(self):
        """無放回重排只檢驗順序,總和必須不變。"""
        rs = [1.0, -1.0, 2.0, -0.5]
        result = self.FakeResult(rs)
        output = montecarlo.run(result, runs=50, mode="shuffle", seed=5)

        self.assertEqual(output.trades_per_run, len(rs))

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            montecarlo.run(self.FakeResult([1.0]), mode="magic")

    def test_seeded_runs_are_reproducible(self):
        result = self.FakeResult([1.0, -1.0, 2.0, -1.0] * 10)
        first = montecarlo.run(result, runs=200, seed=42)
        second = montecarlo.run(result, runs=200, seed=42)

        self.assertEqual(first.median_return_pct, second.median_return_pct)


class TestRunnerDoesNotHideFailures(unittest.TestCase):

    def test_a_crashing_strategy_is_recorded_as_an_error_not_as_zero(self):
        """
        舊的 strategy_optimizer 把失敗的標的記成「0% 報酬」,
        於是壞掉的東西會以中性成績參與平均,把整體數字往上拉。
        """
        def explode(candles, params):
            raise RuntimeError("策略壞了")

        run = runner.run_on_slice(wave_candles(200), explode, {"x": 1})

        self.assertFalse(run.ok)
        self.assertIn("策略壞了", run.error)
        self.assertIsNone(run.metrics)
        self.assertIsNone(run.score)

    def test_optimise_returns_every_parameter_set_not_just_the_winner(self):
        """只回傳最好的那組就是 cherry-picking。"""
        param_sets = [{"lookback": n, "stop_pct": 3.0} for n in (3, 5, 8, 13)]
        runs = runner.optimise(
            wave_candles(400), build_dip_buyer, param_sets,
            engine=BacktestEngine(costs=ZERO_COSTS),
        )

        self.assertEqual(len(runs), len(param_sets))

    def test_failed_runs_sort_to_the_bottom_not_the_top(self):
        def sometimes_explode(candles, params):
            if params.get("lookback") == 5:
                raise RuntimeError("boom")
            return build_dip_buyer(candles, params)

        runs = runner.optimise(
            wave_candles(400), sometimes_explode,
            [{"lookback": 5}, {"lookback": 3, "stop_pct": 3.0}],
            engine=BacktestEngine(costs=ZERO_COSTS),
        )

        self.assertIsNotNone(runs[-1].error)


class TestOutOfSampleIsNotUsedToChooseParameters(unittest.TestCase):

    def test_the_oos_slice_is_never_passed_to_the_training_search(self):
        """
        用 OOS 挑參數等於沒有 OOS。這裡記錄每次 build_signal_fn 收到的
        K 棒長度,確認訓練階段拿到的一律是訓練段。
        """
        candles = wave_candles(600)
        seen_lengths = []

        def spy(slice_candles, params):
            seen_lengths.append(len(slice_candles))
            return build_dip_buyer(slice_candles, params)

        report = runner.in_sample_out_of_sample(
            candles, spy,
            [{"lookback": n, "stop_pct": 3.0} for n in (3, 5)],
            oos_ratio=0.3, engine=BacktestEngine(costs=ZERO_COSTS),
        )

        train_length = int(600 * 0.7)
        oos_length = 600 - train_length

        # 前面幾次是訓練搜尋,最後一次才是 OOS
        self.assertEqual(seen_lengths[:-1], [train_length] * (len(seen_lengths) - 1))
        self.assertEqual(seen_lengths[-1], oos_length)
        self.assertIsNotNone(report.chosen_params)

    def test_walk_forward_reports_every_window(self):
        candles = wave_candles(900)
        report = runner.walk_forward(
            candles, build_dip_buyer,
            [{"lookback": n, "stop_pct": 3.0} for n in (3, 5)],
            train_size=300, test_size=150,
            engine=BacktestEngine(costs=ZERO_COSTS),
        )

        self.assertGreaterEqual(len(report.windows), 3)
        self.assertIsNotNone(report.consistency)

    def test_inconsistent_walk_forward_is_warned_about(self):
        report = runner.WalkForwardReport()
        report.oos_runs = []
        runner._summarise_walk_forward(report)

        self.assertTrue(any("沒有任何視窗" in w for w in report.warnings))


class TestEnsemble(unittest.TestCase):

    def _evaluation(self, name, verdict, expectancy=0.2, consistency=1.0):
        stats = metrics_module.Metrics()
        stats.total_trades = 100
        stats.expectancy_r = expectancy

        oos_run = runner.SliceRun(label="OOS", metrics=stats)
        validation = runner.ValidationReport(oos_run=oos_run)

        wf = runner.WalkForwardReport()
        wf.consistency = consistency

        health = scoring.HealthScore(verdict=verdict, score=80.0)
        if verdict == "REJECT":
            health.blockers = ["樣本數不足"]

        return evaluate.StrategyEvaluation(
            name=name, validation=validation, walk_forward=wf, health=health,
        )

    def test_rejected_strategies_get_zero_weight_not_a_small_one(self):
        result = ensemble.build([
            self._evaluation("GOOD", "PASS"),
            self._evaluation("BAD", "REJECT"),
        ])

        weights = result.weights()
        self.assertNotIn("BAD", weights)
        self.assertIn("GOOD", weights)

    def test_rejected_strategies_still_appear_in_the_report(self):
        """只列入選者,看報告的人不會知道有多少東西被試過又丟掉。"""
        result = ensemble.build([self._evaluation("BAD", "REJECT")])

        self.assertEqual(len(result.members), 1)
        self.assertEqual(result.members[0].weight, 0.0)
        self.assertTrue(result.members[0].reason)

    def test_weights_sum_to_one(self):
        result = ensemble.build([
            self._evaluation("A", "PASS", expectancy=0.3),
            self._evaluation("B", "PASS", expectancy=0.2),
            self._evaluation("C", "PASS", expectancy=0.1),
        ])

        self.assertAlmostEqual(sum(result.weights().values()), 1.0, places=3)

    def test_no_single_strategy_exceeds_the_cap(self):
        result = ensemble.build([
            self._evaluation("HUGE", "PASS", expectancy=5.0),
            self._evaluation("A", "PASS", expectancy=0.1),
            self._evaluation("B", "PASS", expectancy=0.1),
        ])

        for weight in result.weights().values():
            self.assertLessEqual(weight, ensemble.MAX_SINGLE_WEIGHT + 1e-6)

    def test_marginal_strategies_are_weighted_down(self):
        result = ensemble.build([
            self._evaluation("PASSER", "PASS", expectancy=0.2),
            self._evaluation("MARGIN", "MARGINAL", expectancy=0.2),
        ])

        weights = result.weights()
        self.assertLess(weights["MARGIN"], weights["PASSER"])

    def test_low_walk_forward_consistency_lowers_the_weight(self):
        result = ensemble.build([
            self._evaluation("STEADY", "PASS", expectancy=0.2, consistency=1.0),
            self._evaluation("LUCKY", "PASS", expectancy=0.2, consistency=0.3),
        ])

        weights = result.weights()
        self.assertLess(weights["LUCKY"], weights["STEADY"])

    def test_an_empty_ensemble_is_a_valid_answer(self):
        result = ensemble.build([self._evaluation("BAD", "REJECT")])

        self.assertEqual(result.active, [])
        self.assertTrue(any("正確的結果" in w for w in result.warnings))

    def test_a_single_survivor_is_flagged_as_not_really_a_portfolio(self):
        result = ensemble.build([
            self._evaluation("ONLY", "PASS"),
            self._evaluation("BAD", "REJECT"),
        ])

        self.assertTrue(any("單押" in w for w in result.warnings))


class TestEvaluateEndToEnd(unittest.TestCase):

    def test_full_pipeline_produces_a_verdict(self):
        evaluation = evaluate.evaluate(
            wave_candles(900), build_dip_buyer,
            [{"lookback": n, "stop_pct": 3.0} for n in (3, 5, 8)],
            name="DIP", symbol="TEST/USDT",
            monte_carlo_runs=200, seed=1,
            engine=BacktestEngine(costs=ZERO_COSTS),
        )

        self.assertIn(evaluation.verdict, ("PASS", "MARGINAL", "REJECT"))
        self.assertIsNotNone(evaluation.validation)
        self.assertIsNotNone(evaluation.health)
        self.assertTrue(evaluation.summary_lines())

    def test_too_little_data_skips_walk_forward_instead_of_faking_it(self):
        evaluation = evaluate.evaluate(
            wave_candles(200), build_dip_buyer, [{"lookback": 3, "stop_pct": 3.0}],
            name="DIP", monte_carlo_runs=50, seed=1,
            engine=BacktestEngine(costs=ZERO_COSTS),
        )

        self.assertTrue(evaluation.walk_forward.warnings)
        self.assertEqual(evaluation.walk_forward.windows, [])

    def test_no_parameter_sets_is_an_error_not_a_pass(self):
        evaluation = evaluate.evaluate(wave_candles(400), build_dip_buyer, [])

        self.assertEqual(evaluation.verdict, "ERROR")


if __name__ == "__main__":
    unittest.main()


class TestPipelineBridgeValidatesWhatActuallyTrades(unittest.TestCase):
    """
    系統裡有兩組策略:agmcis/strategy/builtin.py(live 在用)與
    strategies/*.py(舊的模組式)。Phase 8 的 Lab 原本只驗第二組 ——
    也就是說 LIVE SAFETY GATE 第 4 項驗的是**一組永遠不會下單的策略**。

    那是 Phase 6 與 Phase 9 處理過兩次的同一類問題:兩條路徑,
    而實際生效的是哪一條沒有人說得清楚。
    """

    def _frame(self, count=500):
        import math

        import pandas as pd

        rows = []
        for i in range(count):
            trend = i * 0.12
            wave = 9.0 * math.sin(i / 10.0) + 3.0 * math.sin(i / 3.0)
            close = 100 + trend + wave
            open_ = 100 + (i - 1) * 0.12 + 9.0 * math.sin((i - 1) / 10.0) \
                + 3.0 * math.sin((i - 1) / 3.0)
            rows.append({
                "timestamp": i * 3_600_000,
                "open": open_,
                "high": max(open_, close) + 0.7,
                "low": min(open_, close) - 0.7,
                "close": close,
                "volume": 1200.0 + 400.0 * abs(math.sin(i / 5.0)),
            })
        return pd.DataFrame(rows)

    def test_the_bridge_uses_the_real_strategy_registry(self):
        """
        複製一份策略邏輯到 Lab 裡,會讓驗的東西與 live 用的東西悄悄分岔。
        """
        import inspect

        from agmcis.lab import pipeline_bridge

        source = inspect.getsource(pipeline_bridge)
        self.assertIn("StrategyRegistry", source)
        self.assertIn("registry.consensus", source)

    def test_indicators_at_a_row_match_the_live_calculation(self):
        """
        Lab 逐根取指標,live 用 indicators.compute() 算最後一根。
        兩邊必須得到同一組數字,否則 Lab 驗的是另一套訊號。
        """
        from agmcis.analysis import indicators as indicators_module
        from agmcis.lab import pipeline_bridge

        frame = self._frame(300)
        precomputed = pipeline_bridge.precompute(frame)

        live = indicators_module.compute(frame, "X", "1h")
        lab = pipeline_bridge.indicators_at(precomputed, len(frame) - 1)

        for field in ("ema20", "ema50", "rsi", "macd", "adx", "atr"):
            with self.subTest(field=field):
                self.assertAlmostEqual(
                    getattr(lab, field), getattr(live, field), places=6,
                )

    def test_warmup_rows_are_reported_as_unusable(self):
        """指標還沒暖機完就進場,等於用不存在的資訊做決定。"""
        from agmcis.lab import pipeline_bridge

        frame = pipeline_bridge.precompute(self._frame(200))

        self.assertFalse(pipeline_bridge.indicators_at(frame, 3).data_ok)

    def test_the_bridge_produces_signals(self):
        """接錯的症狀是「一筆都不交易」,而那看起來跟「條件很嚴」一樣。"""
        from agmcis.lab import pipeline_bridge

        frame = self._frame(500)
        signal_fn = pipeline_bridge.build_signal_fn(frame, {"min_score": 0})

        signals = [
            signal_fn(None, i) for i in range(pipeline_bridge.WARMUP_BARS, 500)
        ]

        self.assertGreater(len([s for s in signals if s]), 0)

    def test_every_signal_carries_a_stop_on_the_correct_side(self):
        from agmcis.lab import pipeline_bridge

        frame = self._frame(500)
        signal_fn = pipeline_bridge.build_signal_fn(frame, {"min_score": 0})

        checked = 0
        for i in range(pipeline_bridge.WARMUP_BARS, 500):
            signal = signal_fn(None, i)
            if not signal:
                continue

            checked += 1
            price = float(frame["close"].iloc[i])
            if signal["direction"] == "做多":
                self.assertLess(signal["stop_loss"], price)
            else:
                self.assertGreater(signal["stop_loss"], price)

        self.assertGreater(checked, 0)

    def test_a_higher_score_threshold_produces_fewer_signals(self):
        """
        這正是 Lab 要回答的取捨:嚴一點的門檻是不是真的換來更好的期望值,
        還是只是讓樣本變小到看不出東西。
        """
        from agmcis.lab import pipeline_bridge

        frame = self._frame(500)

        def count(min_score):
            fn = pipeline_bridge.build_signal_fn(frame, {"min_score": min_score})
            return len([
                s for s in (
                    fn(None, i)
                    for i in range(pipeline_bridge.WARMUP_BARS, 500)
                ) if s
            ])

        self.assertGreaterEqual(count(0), count(75))

    def test_only_one_tunable_parameter(self):
        """參數越多越容易在歷史上擬合出漂亮的曲線。"""
        from agmcis.lab import pipeline_bridge

        keys = set()
        for params in pipeline_bridge.default_param_sets():
            keys.update(params)

        self.assertEqual(keys, {"min_score"})


class TestTheScanValidatesTheLivePipeline(unittest.TestCase):

    def test_the_live_pipeline_is_marked_in_the_report(self):
        import strategy_optimizer

        self.assertEqual(strategy_optimizer.LIVE_PIPELINE_NAME, "LIVE_PIPELINE")

    def test_the_warning_names_the_legacy_strategies(self):
        """
        報告要說清楚哪些策略 live 不會用 ——
        看報告的人不該需要去翻程式碼才知道。
        """
        import strategy_optimizer

        warning = strategy_optimizer.LEGACY_STRATEGY_WARNING
        self.assertIn("永遠不會下單", warning)
        for name, _ in strategy_optimizer.LEGACY_STRATEGIES:
            self.assertIn(name, warning)

    def test_the_live_pipeline_ranks_first(self):
        """它不是比較好,是**它才是會下單的那一個**。"""
        import strategy_optimizer

        rows = [
            {"verdict": "PASS", "health_score": 99.0, "oos_expectancy_r": 5.0,
             "is_live_pipeline": False},
            {"verdict": "REJECT", "health_score": 0.0, "oos_expectancy_r": 0.1,
             "is_live_pipeline": True},
        ]

        ranked = sorted(rows, key=strategy_optimizer._rank_key)
        self.assertTrue(ranked[0]["is_live_pipeline"])
