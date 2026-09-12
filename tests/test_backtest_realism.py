"""
回測的三個現實因素(Master Prompt 第三十三節)與成本敏感度(第三十七節)。

回測與實盤的差距幾乎都來自「回測假設了一件實盤做不到的事」。
這三項各對應一個那樣的假設:

  Partial Fill    回測假設想開多大就開多大,實盤在流動性不足時只成交一部分。
  Partial Close   回測假設一次全平,實盤會分批收。
  Trailing Stop   回測假設停損不動,實盤會跟著走。

以及一個不能靠加減估出來的問題:成本比預期高的時候會怎樣。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.backtest.costs import CostModel
from agmcis.backtest.engine import BacktestEngine

NO_COSTS = CostModel(
    maker_fee=0.0, taker_fee=0.0, slippage_pct=0.0,
    spread_pct=0.0, funding_rate_8h=0.0,
)


def candles(closes, warmup=60, start=100.0):
    """
    暖機段是平的,之後照 closes 走。high / low 貼著 close ——
    這樣測試裡的觸價判定完全由 closes 決定,不會被隨機的影線干擾。
    """
    rows = []
    for i in range(warmup):
        rows.append({"time": i, "open": start, "high": start,
                     "low": start, "close": start, "volume": 1000.0})

    # 訊號在第 warmup 根產生,第 warmup+1 根的開盤成交。多插一根平的,
    # 讓進場價剛好是 start —— 這樣測試裡的 1R / 2R 目標價算得出來。
    closes = [start] + list(closes)
    previous = start
    for offset, close in enumerate(closes):
        rows.append({
            "time": warmup + offset,
            "open": previous,
            "high": max(previous, close),
            "low": min(previous, close),
            "close": close,
            "volume": 1000.0,
        })
        previous = close
    return rows


def long_once(stop_loss=95.0, take_profit=None, at=60):
    """只在第 `at` 根產生一次做多訊號。"""
    def signal_fn(history, index):
        if index != at:
            return None
        return {
            "direction": "做多",
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        }
    return signal_fn


def engine(**kwargs):
    kwargs.setdefault("costs", NO_COSTS)
    kwargs.setdefault("start_balance", 10000.0)
    return BacktestEngine(**kwargs)


class TestPartialFill(unittest.TestCase):
    """
    回測假設永遠全額成交,等於把「這個策略的部位大小市場吃不吃得下」
    這個問題整個跳過。
    """

    def test_a_full_fill_is_the_default(self):
        result = engine().run(candles([110.0] * 10), long_once(), warmup=60)
        trade = result.trades[0]

        self.assertEqual(trade.fill_ratio, 1.0)
        self.assertFalse(trade.is_partially_filled)
        self.assertEqual(trade.quantity, trade.requested_quantity)

    def test_a_partial_fill_shrinks_the_position(self):
        result = engine(
            fill_model=lambda candle, notional: 0.6,
        ).run(candles([110.0] * 10), long_once(), warmup=60)

        trade = result.trades[0]
        self.assertTrue(trade.is_partially_filled)
        self.assertAlmostEqual(trade.fill_ratio, 0.6, places=6)
        self.assertAlmostEqual(
            trade.quantity, trade.requested_quantity * 0.6, places=6,
        )

    def test_a_partial_fill_reduces_the_profit_proportionally(self):
        """成交六成就只賺六成。這是回測與實盤最常見的差距來源。"""
        full = engine().run(candles([110.0] * 10), long_once(), warmup=60)
        partial = engine(
            fill_model=lambda candle, notional: 0.6,
        ).run(candles([110.0] * 10), long_once(), warmup=60)

        self.assertAlmostEqual(
            partial.trades[0].net_pnl, full.trades[0].net_pnl * 0.6, places=4,
        )

    def test_a_zero_fill_means_no_position(self):
        result = engine(
            fill_model=lambda candle, notional: 0.0,
        ).run(candles([110.0] * 10), long_once(), warmup=60)

        self.assertEqual(result.trades, [])
        self.assertEqual(result.skipped_signals, 1)

    def test_a_fill_model_cannot_give_more_than_requested(self):
        """
        交易所不會給你比你要的更多。模型回 1.5 是設定錯誤,
        但這裡夾住而不是拋例外 —— 夾住的方向是保守的。
        """
        result = engine(
            fill_model=lambda candle, notional: 1.5,
        ).run(candles([110.0] * 10), long_once(), warmup=60)

        self.assertEqual(result.trades[0].fill_ratio, 1.0)

    def test_the_fill_model_sees_the_bar_and_the_notional(self):
        """
        流動性模型需要知道「這一根有多少量」與「我要吃多大」——
        少了任一個就只能回一個固定比例,那不是流動性模型。
        """
        seen = []

        engine(
            fill_model=lambda candle, notional: seen.append(
                (candle["volume"], notional)
            ) or 1.0,
        ).run(candles([110.0] * 10), long_once(), warmup=60)

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], 1000.0)
        self.assertGreater(seen[0][1], 0)


class TestPartialClose(unittest.TestCase):

    STAGES = ((1.0, 0.5), (2.0, 0.5))

    def test_reaching_tp1_realises_half(self):
        # 進場 ~100、停損 95,所以 1R = 105
        result = engine(take_profit_stages=self.STAGES).run(
            candles([106.0, 106.0, 106.0]), long_once(), warmup=60,
        )
        trade = result.trades[0]

        self.assertEqual(trade.tp_stage, 1)
        self.assertAlmostEqual(trade.closed_fraction, 0.5, places=6)
        self.assertGreater(trade.realized_partial, 0)

    def test_the_partial_is_recorded_with_its_price(self):
        result = engine(take_profit_stages=self.STAGES).run(
            candles([106.0] * 3), long_once(), warmup=60,
        )
        partials = result.trades[0].partials

        self.assertEqual(len(partials), 1)
        self.assertEqual(partials[0]["stage"], 1)
        self.assertAlmostEqual(partials[0]["fraction"], 0.5, places=6)

    def test_a_stage_is_not_taken_twice(self):
        result = engine(take_profit_stages=self.STAGES).run(
            candles([106.0, 107.0, 108.0, 109.0]), long_once(), warmup=60,
        )

        stages = [p["stage"] for p in result.trades[0].partials]
        self.assertEqual(len(stages), len(set(stages)))

    def test_the_final_stage_is_left_to_the_normal_exit_path(self):
        """
        最後一階由全平路徑處理。在這裡收掉會留下一個 0 比例的部位,
        而那個部位之後每一根都會被當成還開著。
        """
        result = engine(take_profit_stages=self.STAGES).run(
            candles([106.0, 112.0, 112.0]), long_once(), warmup=60,
        )
        trade = result.trades[0]

        self.assertLess(trade.closed_fraction, 1.0)
        self.assertIsNotNone(trade.exit_reason)

    def test_the_whole_trade_pnl_includes_the_partials(self):
        """
        一筆「TP1 收 +90、剩下虧 -20」如果被統計成虧損交易,
        勝率與期望值都會被記反。
        """
        # 先衝到 106(收一半),再跌回停損
        result = engine(take_profit_stages=self.STAGES).run(
            candles([106.0, 100.0, 94.0]), long_once(), warmup=60,
        )
        trade = result.trades[0]

        self.assertGreater(trade.realized_partial, 0)
        self.assertAlmostEqual(
            trade.net_pnl,
            trade._final_leg_pnl + trade.realized_partial,
            places=6,
        )

    def test_the_balance_is_not_credited_twice(self):
        """
        分批那部分在發生的當下就加進餘額了。平倉時再加一次等於憑空生錢。
        """
        result = engine(take_profit_stages=self.STAGES).run(
            candles([106.0, 100.0, 94.0]), long_once(), warmup=60,
        )
        trade = result.trades[0]

        expected = 10000.0 + trade.net_pnl
        self.assertAlmostEqual(result.end_balance, expected, places=4)

    def test_fractions_that_do_not_add_up_are_rejected(self):
        with self.assertRaises(ValueError):
            BacktestEngine(take_profit_stages=((1.0, 0.3), (2.0, 0.3)))

    def test_no_stages_behaves_exactly_as_before(self):
        """
        關掉時的行為必須與加這個功能之前**完全相同** ——
        一個會讓歷史結論悄悄改變的引擎升級,等於把過去的驗證全部作廢。
        """
        rows = candles([106.0, 100.0, 94.0])
        plain = engine().run(rows, long_once(), warmup=60)
        staged_off = engine(take_profit_stages=None).run(rows, long_once(), warmup=60)

        self.assertAlmostEqual(
            plain.end_balance, staged_off.end_balance, places=8,
        )


class TestTrailingStop(unittest.TestCase):

    def test_a_percentage_trail_tightens_the_stop(self):
        result = engine(
            trailing={"mode": "percent", "gap_pct": 3.0},
        ).run(candles([104.0, 108.0, 112.0, 90.0]), long_once(), warmup=60)

        trade = result.trades[0]
        self.assertGreater(trade.stop_moves, 0)
        self.assertGreater(trade.stop_loss, 95.0)

    def test_the_stop_never_loosens(self):
        """
        每一個移動停損實作都有機會在某個邊界情況把停損往外推,
        而那一次就足以把一筆該小賺的交易變成大虧。
        """
        result = engine(
            trailing={"mode": "percent", "gap_pct": 3.0},
        ).run(
            candles([108.0, 112.0, 104.0, 103.0, 102.0]),
            long_once(), warmup=60,
        )
        trade = result.trades[0]

        # 價格回落之後停損不該跟著往下
        self.assertGreaterEqual(trade.stop_loss, 95.0)

    def test_a_trailing_stop_turns_a_round_trip_into_a_win(self):
        """
        這是移動停損存在的理由:衝上去又跌回原點的行情,
        沒有移動停損是白忙一場,有的話會留下一部分。
        """
        rows = candles([104.0, 110.0, 116.0, 99.0, 99.0])

        plain = engine().run(rows, long_once(), warmup=60)
        trailed = engine(
            trailing={"mode": "percent", "gap_pct": 3.0},
        ).run(rows, long_once(), warmup=60)

        self.assertGreater(trailed.end_balance, plain.end_balance)

    def test_an_atr_trail_needs_an_atr_function(self):
        """
        算不出 ATR 就不移動。一個猜出來的停損價比沒有移動更糟 ——
        它看起來像保護。
        """
        result = engine(
            trailing={"mode": "atr", "multiple": 2.0, "atr_fn": lambda h, i: None},
        ).run(candles([110.0] * 5), long_once(), warmup=60)

        self.assertEqual(result.trades[0].stop_moves, 0)

    def test_an_atr_trail_moves_when_the_atr_is_available(self):
        result = engine(
            trailing={"mode": "atr", "multiple": 2.0, "atr_fn": lambda h, i: 1.0},
        ).run(candles([104.0, 108.0, 112.0, 90.0]), long_once(), warmup=60)

        self.assertGreater(result.trades[0].stop_moves, 0)

    def test_an_unknown_mode_is_an_error(self):
        with self.assertRaises(ValueError):
            engine(trailing={"mode": "魔法"}).run(
                candles([110.0] * 5), long_once(), warmup=60,
            )

    def test_the_trail_uses_the_close_not_the_intrabar_extreme(self):
        """
        跟著極值走需要一個這個引擎在別處都拒絕的假設:
        「價格先到極值、才反轉」。用收盤價就沒有這個問題,
        代價是抓到的利潤比真實的逐筆移動停損少一點 —— 那是低估,
        是可以接受的方向。

        做法:一根收盤 100 但最高衝到 130 的 K 棒。用極值算的停損會
        落在 126 附近,用收盤價算的落在 97 —— 兩者差很遠。
        """
        rows = candles([100.0])
        spike = dict(rows[-1])
        spike["high"] = 130.0
        rows[-1] = spike
        rows.append({"time": 999, "open": 100.0, "high": 100.0,
                     "low": 100.0, "close": 100.0, "volume": 1000.0})

        result = engine(
            trailing={"mode": "percent", "gap_pct": 3.0},
        ).run(rows, long_once(), warmup=60)

        trade = result.trades[0]
        self.assertLess(trade.stop_loss, 100.0)

    def test_the_r_multiple_uses_the_original_stop(self):
        """
        用移動後的停損算 R 倍數,每一筆移動過停損的交易 R 倍數都會暴增 ——
        而那是計算方式造成的,不是策略變好了。
        """
        result = engine(
            trailing={"mode": "percent", "gap_pct": 3.0},
        ).run(candles([104.0, 108.0, 112.0, 90.0]), long_once(), warmup=60)

        trade = result.trades[0]
        self.assertEqual(trade.original_stop_loss, 95.0)
        self.assertNotEqual(trade.stop_loss, trade.original_stop_loss)
        # R 倍數應該是個合理的數字,不是因為分母趨近 0 而爆掉
        self.assertLess(abs(trade.r_multiple), 10)

    def test_no_trailing_behaves_exactly_as_before(self):
        rows = candles([104.0, 108.0, 112.0, 90.0])
        plain = engine().run(rows, long_once(), warmup=60)
        off = engine(trailing=None).run(rows, long_once(), warmup=60)

        self.assertAlmostEqual(plain.end_balance, off.end_balance, places=8)


class TestCostSensitivity(unittest.TestCase):
    """
    成本敏感度必須**重跑回測**,不能在結果上做加減:成本變高會改變
    哪些交易還有得賺、強平價在哪、以及成交價本身。
    """

    def _run(self, closes, **kwargs):
        from agmcis.lab import cost_sensitivity

        rows = candles(closes)

        def run_backtest(costs):
            return BacktestEngine(costs=costs, start_balance=10000.0).run(
                rows, long_once(), warmup=60,
            )

        return cost_sensitivity.run(run_backtest, CostModel(), **kwargs)

    def test_scaling_does_not_mutate_the_original_model(self):
        """
        原地修改會讓同一個模型在多次呼叫之間累積放大,
        而那種錯誤在結果裡看起來只是「敏感度比想像中高」。
        """
        from agmcis.lab.cost_sensitivity import scaled_costs

        base = CostModel()
        original_fee = base.taker_fee

        scaled_costs(base, 3.0)
        scaled_costs(base, 3.0)

        self.assertEqual(base.taker_fee, original_fee)

    def test_scaling_multiplies_the_right_fields(self):
        from agmcis.lab.cost_sensitivity import scaled_costs

        base = CostModel()
        doubled = scaled_costs(base, 2.0)

        self.assertAlmostEqual(doubled.taker_fee, base.taker_fee * 2)
        self.assertAlmostEqual(doubled.slippage_pct, base.slippage_pct * 2)

    def test_exchange_rules_are_not_scaled(self):
        """
        清算費是交易所規則,不是我們的估計誤差。放大它只是製造雜訊。
        """
        from agmcis.lab.cost_sensitivity import scaled_costs

        base = CostModel()
        self.assertEqual(
            scaled_costs(base, 5.0).liquidation_fee, base.liquidation_fee,
        )

    def test_higher_costs_reduce_the_return(self):
        report = self._run([110.0] * 5, multipliers=(1.0, 5.0))

        self.assertEqual(len(report.points), 2)
        self.assertLess(
            report.points[1].total_return_pct,
            report.points[0].total_return_pct,
        )

    def test_a_strategy_with_no_edge_at_baseline_says_so(self):
        """
        一個本來就沒有優勢的策略,「優勢在幾倍時消失」這個問題沒有意義。
        """
        report = self._run([94.0, 94.0, 94.0], multipliers=(1.0, 2.0))

        self.assertEqual(report.verdict, "NO_EDGE_AT_BASELINE")
        self.assertIsNone(report.break_even_multiplier)

    def test_a_failing_backtest_is_reported_not_silently_dropped(self):
        from agmcis.lab import cost_sensitivity

        def explode(costs):
            raise RuntimeError("回測壞了")

        report = cost_sensitivity.run(
            explode, CostModel(), multipliers=(1.0,),
        )

        self.assertEqual(report.verdict, "NO_RESULT")
        self.assertTrue(report.warnings)

    def test_a_missing_baseline_is_warned_about(self):
        """沒有 1.0 倍就沒有基準線,退化幅度無從判斷。"""
        report = self._run([110.0] * 5, multipliers=(2.0, 3.0))

        self.assertTrue(any("1.0" in w for w in report.warnings))

    def test_surviving_the_highest_multiple_is_not_a_promise(self):
        report = self._run([130.0] * 5, multipliers=(1.0, 1.5))

        if report.verdict == "ROBUST" and report.break_even_multiplier is None:
            self.assertTrue(
                any("測試範圍不夠寬" in w for w in report.warnings),
            )

    def test_the_summary_is_readable(self):
        report = self._run([110.0] * 5, multipliers=(1.0, 2.0))
        text = "\n".join(report.summary_lines())

        self.assertIn("成本敏感度", text)
        self.assertIn("判定", text)


if __name__ == "__main__":
    unittest.main()
