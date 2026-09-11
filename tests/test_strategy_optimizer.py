"""
多標的 × 多策略驗證掃描,以及它餵給 live 評分的加權。

最重要的一條:`generate_optimizer_bonus` 的輸出會**直接加進 smart_ranking
的最終分數**,也就是直接影響實際下單。所以那條路徑不能建立在回測報酬率上。
"""
import math
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import generate_optimizer_bonus
import strategy_optimizer
from agmcis.backtest import legacy


def wavy_frame(count=800, base=100.0):
    rows = []
    for i in range(count):
        trend = i * 0.05
        wave = 12.0 * math.sin(i / 11.0) + 4.0 * math.sin(i / 3.0)
        close = base + trend + wave
        open_ = base + (i - 1) * 0.05 + 12.0 * math.sin((i - 1) / 11.0) \
            + 4.0 * math.sin((i - 1) / 3.0)
        rows.append({
            "timestamp": i * 3_600_000,
            "open": open_,
            "high": max(open_, close) + 0.8,
            "low": min(open_, close) - 0.8,
            "close": close,
            "volume": 1000.0 + 400.0 * abs(math.sin(i / 5.0)),
        })
    return legacy.add_indicators(pd.DataFrame(rows))


class TestSliceOffsetMapping(unittest.TestCase):
    """
    Lab 會把切好的子區間傳給策略,但舊策略模組的指標掛在整個 DataFrame 上。
    對位錯了不會拋例外,只會安靜地用錯的 K 棒算訊號。
    """

    def test_a_slice_maps_back_to_the_same_rows_as_the_full_frame(self):
        import strategies.ema_strategy as ema_strategy

        df = wavy_frame(400)
        candles = df.to_dict("records")
        build_signal, _ = strategy_optimizer._make_builders(df, ema_strategy)

        full_fn = build_signal(candles, {"atr_stop_multiple": 2.0})
        offset = 120
        slice_fn = build_signal(candles[offset:], {"atr_stop_multiple": 2.0})

        for local_index in range(0, 200, 7):
            with self.subTest(local_index=local_index):
                self.assertEqual(
                    slice_fn(None, local_index),
                    full_fn(None, local_index + offset),
                )

    def test_an_unknown_slice_start_falls_back_to_offset_zero(self):
        import strategies.ema_strategy as ema_strategy

        df = wavy_frame(200)
        build_signal, _ = strategy_optimizer._make_builders(df, ema_strategy)
        fn = build_signal([{"time": 999_999_999, "open": 1, "high": 1,
                            "low": 1, "close": 1, "volume": 1}], {})

        self.assertIsNone(fn(None, 0))


class TestIndicatorsAreCausal(unittest.TestCase):
    """
    指標是在整段資料上算的,包含 OOS 那一段。
    只要每一列只依賴它自己以前的價格,就不構成洩漏 —— 這裡驗證這件事。
    """

    def test_indicator_values_do_not_change_when_future_candles_are_added(self):
        short = wavy_frame(300)
        long = wavy_frame(800)

        for column in ("ema20", "ema50", "rsi", "macd", "atr", "adx", "vol_ma20"):
            with self.subTest(column=column):
                a = float(short[column].iloc[250])
                b = float(long[column].iloc[250])
                if a != a and b != b:      # 兩邊都是 NaN
                    continue
                self.assertAlmostEqual(a, b, places=6)


class TestScanDoesNotHideFailures(unittest.TestCase):

    def test_a_symbol_that_fails_to_load_is_an_error_not_a_zero_percent_result(self):
        """
        舊版把失敗的標的記成「0% 報酬」,於是它會以中性成績參與平均,
        而且看報告的人完全不知道有東西壞了。
        """
        def explode(symbol, **kwargs):
            raise ValueError(f"{symbol} 資料品質不合格")

        with patch.object(strategy_optimizer, "evaluate_symbol", side_effect=explode):
            result = strategy_optimizer.get_strategy_optimizer(
                symbols=["BROKEN/USDT"],
            )

        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["symbol_best"][0]["verdict"], "ERROR")
        self.assertIsNone(result["best_overall"])

    def test_errors_are_not_counted_in_the_strategy_pass_rate(self):
        rows = [
            {"strategy": "A", "verdict": "PASS", "health_score": 80.0},
            {"strategy": "A", "verdict": "ERROR", "health_score": 0.0},
        ]
        summary = strategy_optimizer._summarise_by_strategy(rows)

        entry = summary[0]
        self.assertEqual(entry["evaluated"], 1)
        self.assertEqual(entry["errors"], 1)
        self.assertEqual(entry["pass_rate"], 1.0)
        self.assertEqual(entry["avg_health"], 80.0)

    def test_best_overall_is_none_when_nothing_passed(self):
        rows = [
            {"strategy": "A", "verdict": "REJECT", "health_score": 40.0,
             "oos_expectancy_r": 0.1},
            {"strategy": "B", "verdict": "REJECT", "health_score": 50.0,
             "oos_expectancy_r": 0.2},
        ]

        self.assertIsNone(strategy_optimizer._best_overall(rows))


class TestRankingIsNotByReturn(unittest.TestCase):

    def test_a_rejected_result_never_outranks_a_passing_one(self):
        passing = {"verdict": "PASS", "health_score": 10.0, "oos_expectancy_r": 0.01}
        rejected = {"verdict": "REJECT", "health_score": 99.0,
                    "oos_expectancy_r": 5.0}

        ranked = sorted([rejected, passing], key=strategy_optimizer._rank_key)
        self.assertIs(ranked[0], passing)

    def test_return_pct_is_not_part_of_the_rank_key(self):
        low_return = {"verdict": "PASS", "health_score": 80.0,
                      "oos_expectancy_r": 0.3, "oos_return_pct": 1.0}
        high_return = {"verdict": "PASS", "health_score": 40.0,
                       "oos_expectancy_r": 0.1, "oos_return_pct": 900.0}

        ranked = sorted([high_return, low_return], key=strategy_optimizer._rank_key)
        self.assertIs(ranked[0], low_return)


class TestLiveScoreBonus(unittest.TestCase):
    """這個加權會直接進入 smart_ranking 的最終分數。"""

    def test_rejected_symbols_get_zero_not_a_penalty(self):
        """沒通過驗證不是這個標的的錯。中性就是中性。"""
        self.assertEqual(
            generate_optimizer_bonus.verdict_to_bonus("REJECT", 0.5), 0,
        )

    def test_failed_validation_gets_zero_not_a_penalty(self):
        """舊版給 -10 —— 因為「我們沒能驗證它」而懲罰一個標的。"""
        self.assertEqual(
            generate_optimizer_bonus.verdict_to_bonus("ERROR", None), 0,
        )

    def test_passing_symbols_are_graded_by_expectancy_not_return(self):
        weak = generate_optimizer_bonus.verdict_to_bonus("PASS", 0.05)
        strong = generate_optimizer_bonus.verdict_to_bonus("PASS", 0.30)

        self.assertLess(weak, strong)
        self.assertEqual(strong, generate_optimizer_bonus.MAX_BONUS)

    def test_bonus_is_capped(self):
        self.assertEqual(
            generate_optimizer_bonus.verdict_to_bonus("PASS", 99.0),
            generate_optimizer_bonus.MAX_BONUS,
        )

    def test_marginal_gets_only_a_small_bonus(self):
        self.assertEqual(
            generate_optimizer_bonus.verdict_to_bonus("MARGINAL", 0.30),
            generate_optimizer_bonus.MARGINAL_BONUS,
        )

    def test_a_pass_without_positive_expectancy_gets_nothing(self):
        self.assertEqual(
            generate_optimizer_bonus.verdict_to_bonus("PASS", -0.1), 0,
        )

    def test_build_bonus_skips_entries_without_a_symbol(self):
        bonus = generate_optimizer_bonus.build_bonus({
            "symbol_best": [
                {"symbol": None, "verdict": "PASS", "oos_expectancy_r": 0.3},
                {"symbol": "BTC/USDT", "verdict": "PASS", "oos_expectancy_r": 0.3},
            ],
        })

        self.assertEqual(bonus, {"BTC/USDT": generate_optimizer_bonus.MAX_BONUS})


class TestSurvivorshipBiasIsDeclared(unittest.TestCase):

    def test_the_scan_output_carries_the_warning(self):
        """
        用「當前」成交量前 N 名回測過去,已下市的標的不在樣本裡。
        這一層修不掉,但必須寫在輸出裡,不能靜靜地當它不存在。
        """
        with patch.object(strategy_optimizer, "evaluate_symbol", return_value=[]):
            result = strategy_optimizer.get_strategy_optimizer(symbols=["BTC/USDT"])

        self.assertTrue(any("survivorship" in w.lower()
                            for w in result["warnings"]))


class TestEndToEndScan(unittest.TestCase):

    def test_a_full_symbol_scan_produces_verdicts_for_every_strategy(self):
        """live 管線 + 每個舊策略都要有結果。"""
        df = wavy_frame(800)

        with patch.object(strategy_optimizer, "load_data", return_value=df):
            evaluations = strategy_optimizer.evaluate_symbol(
                "TEST/USDT", monte_carlo_runs=100, seed=1,
            )

        self.assertEqual(
            len(evaluations), len(strategy_optimizer.LEGACY_STRATEGIES) + 1,
        )
        self.assertEqual(
            evaluations[0].name, strategy_optimizer.LIVE_PIPELINE_NAME,
        )
        for evaluation in evaluations:
            with self.subTest(name=evaluation.name):
                self.assertIn(
                    evaluation.verdict, ("PASS", "MARGINAL", "REJECT", "ERROR"),
                )
                self.assertEqual(evaluation.symbol, "TEST/USDT")

    def test_the_scan_reports_every_strategy_not_just_the_best(self):
        df = wavy_frame(800)

        with patch.object(strategy_optimizer, "load_data", return_value=df):
            result = strategy_optimizer.get_strategy_optimizer(
                symbols=["TEST/USDT"], candles=800, seed=1,
            )

        strategies_in_report = {
            row["strategy"] for row in result["all_results"] if row["strategy"]
        }
        self.assertEqual(
            strategies_in_report,
            {name for name, _ in strategy_optimizer.LEGACY_STRATEGIES}
            | {strategy_optimizer.LIVE_PIPELINE_NAME},
        )

    def test_the_live_pipeline_result_is_flagged(self):
        """
        LIVE SAFETY GATE 只認這個旗標為 True 的結果 ——
        驗證一組永遠不會下單的策略等於沒有驗證。
        """
        df = wavy_frame(800)

        with patch.object(strategy_optimizer, "load_data", return_value=df):
            result = strategy_optimizer.get_strategy_optimizer(
                symbols=["TEST/USDT"], candles=800, seed=1,
            )

        live = [r for r in result["all_results"] if r.get("is_live_pipeline")]

        self.assertEqual(len(live), 1)
        self.assertEqual(live[0]["strategy"], strategy_optimizer.LIVE_PIPELINE_NAME)

    def test_the_live_pipeline_trades_more_than_the_legacy_strategies(self):
        """
        舊策略在 1 小時 K 棒上交易得太少,樣本外永遠湊不到 30 筆。
        live 管線是集成的,訊號密度高得多 —— 那是它才有機會通過驗證的原因。
        """
        df = wavy_frame(1200)

        with patch.object(strategy_optimizer, "load_data", return_value=df):
            result = strategy_optimizer.get_strategy_optimizer(
                symbols=["TEST/USDT"], candles=1200, seed=1,
            )

        rows = {r["strategy"]: r for r in result["all_results"] if r["strategy"]}
        live_trades = rows[strategy_optimizer.LIVE_PIPELINE_NAME]["oos_trades"]
        legacy_best = max(
            rows[name]["oos_trades"]
            for name, _ in strategy_optimizer.LEGACY_STRATEGIES
        )

        self.assertGreater(live_trades, legacy_best)


if __name__ == "__main__":
    unittest.main()
