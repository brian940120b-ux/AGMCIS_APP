"""
出場計畫(Master Prompt 第五十五 ~ 五十八節)。

進場只有一個決定:要不要開。出場有一連串:先收多少、什麼時候把停損
拉到成本、剩下的跟多遠、跟到什麼時候放棄。

出場邏輯的錯誤幾乎都不會當場爆炸 —— 它們會變成一連串「怎麼這筆
賺得比預期少」。所以這組測試最花力氣的地方是邊界:停損會不會在某個
情況下被往外推、同一個 TP 會不會被收兩次、分批之後勝率會不會失真。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.execution import exit_plan


def position(direction="做多", entry=100.0, stop=95.0, stage=0, symbol="BTC/USDT"):
    return {
        "symbol": symbol, "signal": direction,
        "entry_price": entry, "stoploss": stop, "tp_stage": stage,
    }


class TestBuildingThePlan(unittest.TestCase):

    def test_targets_are_r_multiples_of_the_stop_distance(self):
        """
        用 R 倍數而不是固定百分比:停損放 1% 和放 5% 的部位,
        同樣「賺 3%」的意義完全不同。
        """
        plan = exit_plan.build("做多", entry=100.0, stop_loss=95.0)
        prices = [t["price"] for t in plan.targets]

        self.assertEqual(prices, [105.0, 110.0, 115.0])

    def test_a_short_puts_its_targets_below_entry(self):
        plan = exit_plan.build("做空", entry=100.0, stop_loss=105.0)
        prices = [t["price"] for t in plan.targets]

        self.assertEqual(prices, [95.0, 90.0, 85.0])

    def test_a_tighter_stop_gives_tighter_targets(self):
        tight = exit_plan.build("做多", entry=100.0, stop_loss=99.0)
        wide = exit_plan.build("做多", entry=100.0, stop_loss=90.0)

        self.assertLess(tight.targets[0]["price"], wide.targets[0]["price"])

    def test_fractions_must_add_up_to_exactly_one(self):
        """
        少於 1 會留下一個永遠不會被平掉的殘倉;多於 1 平不出來。
        兩種都不是「差一點點」,是計畫本身壞掉。
        """
        with self.assertRaises(ValueError) as caught:
            exit_plan.build("做多", 100.0, 95.0, targets=((1.0, 0.3), (2.0, 0.3)))

        self.assertIn("1.0", str(caught.exception))

    def test_break_even_sits_beyond_entry_not_at_it(self):
        """
        真的放在進場價,兩趟手續費加滑點會讓它變成一筆小虧。
        """
        plan = exit_plan.build("做多", entry=100.0, stop_loss=95.0)

        self.assertGreater(plan.break_even, 100.0)

    def test_a_short_break_even_sits_below_entry(self):
        plan = exit_plan.build("做空", entry=100.0, stop_loss=105.0)

        self.assertLess(plan.break_even, 100.0)

    def test_a_zero_risk_plan_is_rejected(self):
        with self.assertRaises(ValueError):
            exit_plan.build("做多", entry=100.0, stop_loss=100.0)

    def test_an_unknown_direction_is_rejected(self):
        with self.assertRaises(ValueError):
            exit_plan.build("大概往上", entry=100.0, stop_loss=95.0)


class TestTheStopOnlyEverTightens(unittest.TestCase):
    """
    這是整個模組最重要的一條。每一個移動停損的實作都有機會在某個
    邊界情況把停損往外推,而那一次就足以把一筆該小賺的交易變成大虧。
    """

    def test_a_looser_long_stop_is_refused(self):
        self.assertIsNone(exit_plan.tighten_only("做多", 98.0, 95.0))

    def test_a_tighter_long_stop_is_accepted(self):
        self.assertEqual(exit_plan.tighten_only("做多", 95.0, 98.0), 98.0)

    def test_a_looser_short_stop_is_refused(self):
        self.assertIsNone(exit_plan.tighten_only("做空", 102.0, 105.0))

    def test_a_tighter_short_stop_is_accepted(self):
        self.assertEqual(exit_plan.tighten_only("做空", 105.0, 102.0), 102.0)

    def test_an_equal_stop_is_not_a_change(self):
        self.assertIsNone(exit_plan.tighten_only("做多", 98.0, 98.0))

    def test_no_existing_stop_accepts_the_candidate(self):
        self.assertEqual(exit_plan.tighten_only("做多", None, 95.0), 95.0)

    def test_a_missing_candidate_is_not_a_stop(self):
        self.assertIsNone(exit_plan.tighten_only("做多", 95.0, None))

    def test_high_volatility_does_not_loosen_the_stop(self):
        """
        波動變大的時候最想放寬,而那正好是最不該放寬的時候。
        ATR 變大會產生一個比較遠的候選值 —— 它必須被拒絕。
        """
        calm = exit_plan.trail_atr("做多", price=110.0, atr=1.0)
        wild = exit_plan.trail_atr("做多", price=110.0, atr=8.0)

        self.assertIsNotNone(exit_plan.tighten_only("做多", 100.0, calm))
        self.assertIsNone(exit_plan.tighten_only("做多", 100.0, wild))


class TestTrailingModes(unittest.TestCase):

    def test_atr_trailing_scales_with_volatility(self):
        low = exit_plan.trail_atr("做多", price=100.0, atr=1.0)
        high = exit_plan.trail_atr("做多", price=100.0, atr=5.0)

        self.assertGreater(low, high)

    def test_atr_trailing_without_an_atr_gives_nothing(self):
        """
        一個猜出來的停損價比沒有停損更糟:它看起來像保護。
        """
        self.assertIsNone(exit_plan.trail_atr("做多", price=100.0, atr=None))
        self.assertIsNone(exit_plan.trail_atr("做多", price=100.0, atr=0))

    def test_percent_trailing_is_symmetric_in_direction(self):
        long_stop = exit_plan.trail_percent("做多", 100.0, gap_pct=3.0)
        short_stop = exit_plan.trail_percent("做空", 100.0, gap_pct=3.0)

        self.assertAlmostEqual(long_stop, 97.0)
        self.assertAlmostEqual(short_stop, 103.0)

    def test_structure_trailing_uses_the_recent_swing_low(self):
        candles = [{"high": 110 + i, "low": 100 + i} for i in range(10)]
        stop = exit_plan.trail_structure("做多", candles, lookback=10)

        # 最近 10 根的最低點是 100,再往下一點點緩衝
        self.assertLess(stop, 100.0)
        self.assertGreater(stop, 99.0)

    def test_structure_trailing_uses_the_recent_swing_high_for_shorts(self):
        candles = [{"high": 110 + i, "low": 100 + i} for i in range(10)]
        stop = exit_plan.trail_structure("做空", candles, lookback=10)

        self.assertGreater(stop, 119.0)

    def test_structure_trailing_needs_enough_candles(self):
        candles = [{"high": 110, "low": 100}] * 3
        self.assertIsNone(exit_plan.trail_structure("做多", candles, lookback=10))

    def test_broken_candles_give_nothing_rather_than_a_wrong_stop(self):
        candles = [{"high": 110}] * 10
        self.assertIsNone(exit_plan.trail_structure("做多", candles, lookback=10))

    def test_an_unknown_mode_is_an_error_not_a_silent_noop(self):
        plan = exit_plan.build("做多", 100.0, 95.0)

        with self.assertRaises(ValueError):
            exit_plan.decide(plan, position(), price=101.0, trail_mode="魔法")


class TestPartialTakeProfit(unittest.TestCase):

    def _plan(self):
        return exit_plan.build("做多", entry=100.0, stop_loss=95.0)

    def test_tp1_is_taken_when_price_reaches_it(self):
        action = exit_plan.decide(self._plan(), position(), price=105.0)

        self.assertEqual(action.kind, exit_plan.PARTIAL_TP)
        self.assertEqual(action.stage, 1)
        self.assertAlmostEqual(action.fraction, 0.30)

    def test_a_wick_that_touches_tp1_counts(self):
        """
        只看輪詢當下的價格會漏掉「碰到 TP1 又跌回來」——
        那一次 TP1 是真的碰到了,錢是真的可以收的。
        """
        action = exit_plan.decide(
            self._plan(), position(), price=102.0, high=105.5,
        )

        self.assertEqual(action.kind, exit_plan.PARTIAL_TP)

    def test_tp1_is_not_taken_twice(self):
        """
        同一個 stage 收兩次等於平掉 60% 而不是 30%。
        排程重疊與重試都會走到這裡。
        """
        after_tp1 = position(stage=1)
        action = exit_plan.decide(self._plan(), after_tp1, price=105.0)

        self.assertNotEqual(action.kind, exit_plan.PARTIAL_TP)

    def test_stages_are_taken_in_order(self):
        """
        價格直接衝到 TP3 也必須先收 TP1 —— 否則 TP1 與 TP2 那兩份
        永遠不會被收,而計畫裡它們加起來是 60%。
        """
        action = exit_plan.decide(self._plan(), position(), price=120.0)

        self.assertEqual(action.stage, 1)

    def test_the_last_stage_closes_the_whole_remainder(self):
        action = exit_plan.decide(self._plan(), position(stage=2), price=115.0)

        self.assertEqual(action.kind, exit_plan.FULL_TP)
        self.assertEqual(action.stage, 3)

    def test_a_short_takes_profit_on_the_way_down(self):
        plan = exit_plan.build("做空", entry=100.0, stop_loss=105.0)
        action = exit_plan.decide(
            plan, position(direction="做空", stop=105.0), price=95.0, low=94.0,
        )

        self.assertEqual(action.kind, exit_plan.PARTIAL_TP)

    def test_nothing_happens_below_tp1(self):
        action = exit_plan.decide(self._plan(), position(), price=103.0)

        self.assertTrue(action.is_noop)


class TestBreakEven(unittest.TestCase):

    def test_the_stop_moves_to_cost_after_tp1(self):
        plan = exit_plan.build("做多", 100.0, 95.0)
        action = exit_plan.decide(plan, position(stage=1), price=106.0)

        self.assertEqual(action.kind, exit_plan.MOVE_STOP)
        self.assertEqual(action.new_stop, plan.break_even)

    def test_it_does_not_happen_before_tp1(self):
        plan = exit_plan.build("做多", 100.0, 95.0)
        action = exit_plan.decide(plan, position(stage=0), price=101.0)

        self.assertTrue(action.is_noop)

    def test_trailing_never_drops_the_stop_below_cost_after_tp1(self):
        """
        拿過 TP1 之後,移動停損算出一個比成本價還低的候選值時,
        不能採用它 —— 那等於把一筆已經鎖定的小賺變回可能虧損。
        """
        plan = exit_plan.build("做多", 100.0, 95.0)
        pos = position(stage=1, stop=plan.break_even)

        # ATR 很大,算出來的移動停損會落在成本價之下
        action = exit_plan.decide(
            plan, pos, price=106.0, atr=10.0, trail_mode="atr",
        )

        self.assertTrue(action.is_noop, action.reason)


class TestTimeExit(unittest.TestCase):

    def test_a_stale_position_is_closed(self):
        plan = exit_plan.build("做多", 100.0, 95.0)
        action = exit_plan.decide(
            plan, position(), price=100.5, held_hours=100,
        )

        self.assertEqual(action.kind, exit_plan.TIME_EXIT)
        self.assertEqual(action.fraction, 1.0)

    def test_a_fresh_position_is_left_alone(self):
        plan = exit_plan.build("做多", 100.0, 95.0)
        action = exit_plan.decide(
            plan, position(), price=100.5, held_hours=3,
        )

        self.assertTrue(action.is_noop)

    def test_time_exit_applies_to_winners_too(self):
        """
        只在虧損時做時間出場,等於所有虧損部位一直留著 ——
        那是處置效應的程式碼版本。
        """
        plan = exit_plan.build("做多", 100.0, 95.0)
        action = exit_plan.decide(
            plan, position(stage=3, stop=110.0), price=104.0, held_hours=100,
        )

        self.assertEqual(action.kind, exit_plan.TIME_EXIT)

    def test_taking_profit_wins_over_the_clock(self):
        """
        先收錢再談別的:收錢是不可逆的機會(價格會跑掉),
        而時間出場在下一輪還可以做。
        """
        plan = exit_plan.build("做多", 100.0, 95.0)
        action = exit_plan.decide(
            plan, position(), price=105.0, held_hours=100,
        )

        self.assertEqual(action.kind, exit_plan.PARTIAL_TP)


class TestApplyingToARealPosition(unittest.TestCase):

    class FakeTrade:
        def __init__(self):
            self.reduced = []
            self.closed = []

        def reduce_paper_trade(self, symbol, fraction, price, stage, reason=""):
            self.reduced.append((symbol, fraction, stage))
            return {"success": True}

        def close_paper_trade(self, symbol, price, reason=""):
            self.closed.append((symbol, reason))
            return {"success": True}

    def test_a_partial_target_calls_reduce_not_close(self):
        """
        分批停利呼叫全平,會把一筆才走到 1R 的交易整個結束掉。
        """
        trade = self.FakeTrade()
        action, _ = exit_plan.apply_to(
            position(), price=105.0, trade=trade, monitor=lambda *a: True,
        )

        self.assertEqual(action.kind, exit_plan.PARTIAL_TP)
        self.assertEqual(len(trade.reduced), 1)
        self.assertEqual(trade.closed, [])

    def test_the_final_target_calls_close(self):
        trade = self.FakeTrade()
        exit_plan.apply_to(
            position(stage=2), price=115.0, trade=trade, monitor=lambda *a: True,
        )

        self.assertEqual(trade.reduced, [])
        self.assertEqual(len(trade.closed), 1)

    def test_a_stop_move_writes_the_stop_and_touches_nothing_else(self):
        trade = self.FakeTrade()
        written = []

        exit_plan.apply_to(
            position(stage=1), price=106.0, trade=trade,
            monitor=lambda symbol, stop: written.append((symbol, stop)) or True,
        )

        self.assertEqual(len(written), 1)
        self.assertEqual(trade.reduced, [])
        self.assertEqual(trade.closed, [])

    def test_the_plan_uses_the_original_stop_not_the_moved_one(self):
        """
        用現在的停損算 R 倍數,目標會隨著停損一路往上飄 ——
        每移動一次停損,TP2 就變遠一點,最後永遠收不到。
        """
        pos = position(stage=1, stop=104.0)
        pos["original_stoploss"] = 95.0

        plan = exit_plan.plan_for(pos)
        self.assertEqual([t["price"] for t in plan.targets], [105.0, 110.0, 115.0])

    def test_a_position_without_a_stop_produces_no_plan(self):
        pos = position()
        pos["stoploss"] = None

        self.assertIsNone(exit_plan.plan_for(pos))

    def test_one_bad_symbol_does_not_stop_the_round(self):
        """
        在第三個標的掛掉就不再處理後面的出場管理器,
        會讓後面的部位完全沒有人管。
        """
        def price_of(symbol):
            if symbol == "BAD/USDT":
                raise RuntimeError("取價失敗")
            return 105.0

        trade = self.FakeTrade()
        summary = exit_plan.run_exit_plans(
            positions=[
                position(symbol="A/USDT"),
                position(symbol="BAD/USDT"),
                position(symbol="B/USDT"),
            ],
            price_of=price_of, trade=trade, monitor=lambda *a: True,
        )

        self.assertEqual(len(summary["actions"]), 2)
        self.assertEqual(len(summary["errors"]), 1)
        self.assertIn("BAD/USDT", summary["errors"][0])

    def test_a_symbol_without_a_price_is_reported_not_guessed(self):
        summary = exit_plan.run_exit_plans(
            positions=[position()], price_of=lambda s: None,
            trade=self.FakeTrade(), monitor=lambda *a: True,
        )

        self.assertEqual(summary["actions"], [])
        self.assertIn("取不到現價", summary["errors"][0])


if __name__ == "__main__":
    unittest.main()


class TestTheTakeProfitCap(unittest.TestCase):
    """
    position_monitor 看的是 trades.takeprofit,碰到就**整筆**平掉。
    如果那個價格比 TP3 近,分批計畫永遠走不到 TP2 ——
    第一次碰到停利價就被整個平掉了。那不是任何人設計的行為。
    """

    def test_a_near_take_profit_collapses_the_later_stages(self):
        plan = exit_plan.build("做多", 100.0, 95.0, cap=108.0)

        self.assertEqual(len(plan.targets), 2)
        self.assertEqual(plan.targets[-1]["price"], 108.0)

    def test_the_fractions_still_add_up_to_one_after_capping(self):
        plan = exit_plan.build("做多", 100.0, 95.0, cap=108.0)
        total = sum(t["fraction"] for t in plan.targets)

        self.assertAlmostEqual(total, 1.0, places=6)

    def test_the_last_stage_absorbs_everything_that_was_dropped(self):
        plan = exit_plan.build("做多", 100.0, 95.0, cap=108.0)

        # TP1 收 30%,剩下的 70% 全部在 cap 收掉
        self.assertAlmostEqual(plan.targets[0]["fraction"], 0.30, places=6)
        self.assertAlmostEqual(plan.targets[1]["fraction"], 0.70, places=6)

    def test_a_cap_before_tp1_makes_a_single_stage_plan(self):
        plan = exit_plan.build("做多", 100.0, 95.0, cap=102.0)

        self.assertEqual(len(plan.targets), 1)
        self.assertAlmostEqual(plan.targets[0]["fraction"], 1.0, places=6)
        self.assertEqual(plan.targets[0]["price"], 102.0)

    def test_a_far_take_profit_changes_nothing(self):
        plan = exit_plan.build("做多", 100.0, 95.0, cap=500.0)

        self.assertEqual([t["price"] for t in plan.targets], [105.0, 110.0, 115.0])

    def test_a_short_is_capped_on_the_way_down(self):
        plan = exit_plan.build("做空", 100.0, 105.0, cap=92.0)

        self.assertEqual(plan.targets[-1]["price"], 92.0)
        self.assertEqual(len(plan.targets), 2)

    def test_a_take_profit_on_the_wrong_side_is_ignored(self):
        """
        做多的停利放在進場價下方是資料錯誤。拿它夾住目標,
        TP1 會變成一個立刻成立的虧損出場。
        """
        plan = exit_plan.build("做多", 100.0, 95.0, cap=90.0)

        self.assertEqual([t["price"] for t in plan.targets], [105.0, 110.0, 115.0])

    def test_an_unparseable_take_profit_is_ignored(self):
        plan = exit_plan.build("做多", 100.0, 95.0, cap="大概 110 吧")

        self.assertEqual(len(plan.targets), 3)

    def test_the_plan_from_a_position_uses_its_take_profit(self):
        pos = position()
        pos["takeprofit"] = 108.0

        plan = exit_plan.plan_for(pos)
        self.assertEqual(plan.targets[-1]["price"], 108.0)

    def test_both_systems_end_at_the_same_price(self):
        """
        分批計畫的最後一階價格必須等於 position_monitor 看的停利價。
        誰先跑到都一樣,而且不會有一邊留下殘倉。
        """
        pos = position()
        pos["takeprofit"] = 107.5

        plan = exit_plan.plan_for(pos)
        self.assertEqual(plan.targets[-1]["price"], pos["takeprofit"])
