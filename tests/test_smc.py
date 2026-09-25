"""SMC 機械化定義的測試 · 2026-09-25

這批測試守的不是「有沒有跑」,是**定義有沒有被偷偷改掉**。
SMC 最大的風險不是程式錯,是定義被結果牽著走 —— 所以每一條
定義都在這裡釘死一個具體數字。
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from portfolio import smc
from portfolio.event_sim import Setup, bootstrap_positive, run, stats
from portfolio.sim import Bar

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def bar(i, o, h, l, c, minutes=5):
    return Bar(T0 + timedelta(minutes=minutes * i), o, h, l, c, 1.0)


def flat(n, px=100.0, start=0, minutes=5):
    return [bar(start + i, px, px + 0.1, px - 0.1, px, minutes)
            for i in range(n)]


class Fractals(unittest.TestCase):
    def test_swing_high_needs_strictly_higher(self):
        bs = flat(5)
        bs[2] = bar(2, 100, 105, 99.9, 100)          # 中間一根明顯高
        sw = [s for s in smc.swings(bs) if s.high]
        self.assertEqual([s.i for s in sw], [2])
        self.assertEqual(sw[0].price, 105)

    def test_tie_is_not_a_swing(self):
        """平手不算。橫盤時放寬會標出一整排擺動點,訊號變雜訊。"""
        bs = flat(5)
        bs[1] = bar(1, 100, 105, 99.9, 100)
        bs[2] = bar(2, 100, 105, 99.9, 100)          # 與鄰居同高
        self.assertEqual([s for s in smc.swings(bs) if s.high], [])

    def test_confirmed_at_is_k_bars_later(self):
        """前視偏誤的防線:第 i 根的擺動,要第 i+k 根才知道。"""
        bs = flat(7)
        bs[3] = bar(3, 100, 110, 99.9, 100)
        sw = [s for s in smc.swings(bs) if s.high][0]
        self.assertEqual(sw.i, 3)
        self.assertEqual(sw.confirmed_at, 3 + smc.FRACTAL_K)


class Structure(unittest.TestCase):
    def _run(self, highs_at, closes):
        bs = []
        for i, c in enumerate(closes):
            h = highs_at.get(i, c + 0.1)
            bs.append(bar(i, c, max(h, c), min(c - 0.1, c), c))
        return bs

    def test_break_uses_close_not_wick(self):
        """插針穿過再收回來不算突破 —— 與 donchian 同一條理由。"""
        bs = flat(9)
        bs[2] = bar(2, 100, 106, 99.9, 100)          # 擺動高 106
        bs[7] = bar(7, 100, 120, 99.9, 100)          # 影線穿到 120,收在 100
        self.assertEqual([b for b in smc.breaks(bs) if b.up], [])
        bs[7] = bar(7, 100, 120, 99.9, 108)          # 改成收在 108
        self.assertTrue(any(b.up for b in smc.breaks(bs)))

    def test_first_break_after_downtrend_is_choch(self):
        bs = flat(6) + flat(9, px=93.0, start=6)     # 跌一段之後在 93 附近盤
        bs[2] = bar(2, 100, 100.1, 94.0, 100)        # 擺動低 94
        bs[6] = bar(6, 100, 100.2, 92.9, 93.0)       # 收破 94 → 趨勢轉 down
        bs[9] = bar(9, 93, 97.0, 92.9, 93)           # 擺動高 97(鄰居只有 93.1)
        bs[13] = bar(13, 93, 98.5, 92.9, 98.0)       # 收破 97 → CHOCH
        ev = smc.breaks(bs)
        ups = [b for b in ev if b.up]
        self.assertTrue(ups, f"沒有向上突破:{ev}")
        self.assertTrue(ups[-1].choch, "趨勢是 down,向上突破必須標成 CHOCH")

    def test_second_break_same_direction_is_bos_not_choch(self):
        """順勢延續叫 BOS。貼文要的是 CHOCH,兩者不能混為一談。"""
        bs = flat(6) + flat(14, px=93.0, start=6)
        bs[2] = bar(2, 100, 100.1, 94.0, 100)
        bs[6] = bar(6, 100, 100.2, 92.9, 93.0)       # → down
        bs[9] = bar(9, 93, 97.0, 92.9, 93)
        bs[13] = bar(13, 93, 98.5, 92.9, 98.0)       # CHOCH 向上
        bs[16] = bar(16, 98, 103.0, 92.9, 98)        # 新擺動高 103
        bs[19] = bar(19, 98, 105.0, 92.9, 104.0)     # 再破 → BOS
        ups = [b for b in smc.breaks(bs) if b.up]
        self.assertGreaterEqual(len(ups), 2, ups)
        self.assertTrue(ups[0].choch)
        self.assertFalse(ups[-1].choch, "同方向的第二次突破是 BOS")


class Zones(unittest.TestCase):
    def test_fvg_is_the_three_bar_definition(self):
        bs = [bar(0, 100, 101, 99, 100),
              bar(1, 101, 110, 100, 109),
              bar(2, 109, 112, 103, 111)]            # 第0根高 101 < 第2根低 103
        z = smc.fvg(bs, 2, True)
        self.assertIsNotNone(z)
        self.assertEqual((z.lo, z.hi), (101, 103))

    def test_no_fvg_when_bars_overlap(self):
        bs = [bar(0, 100, 105, 99, 104),
              bar(1, 104, 108, 103, 107),
              bar(2, 107, 110, 104, 109)]            # 105 > 104,有重疊
        self.assertIsNone(smc.fvg(bs, 2, True))

    def test_order_block_is_last_opposite_candle(self):
        bs = [bar(0, 100, 101, 99, 100.5),           # 陽
              bar(1, 100.5, 101, 98, 99.0),          # 陰 ← 應該是這根
              bar(2, 99, 106, 99, 105),              # 推進
              bar(3, 105, 112, 105, 111)]            # 突破
        z = smc.order_block(bs, 3, True)
        self.assertEqual(z.i, 1)
        self.assertEqual((z.lo, z.hi), (98, 101))

    def test_order_block_gives_up_rather_than_reach_far_back(self):
        bs = [bar(0, 100, 101, 99, 99.0)] + [
            bar(i, 100, 101, 99, 100.5) for i in range(1, 40)]
        self.assertIsNone(smc.order_block(bs, 39, True, max_lookback=5))

    def test_engulfing_uses_body_not_wick(self):
        bs = [bar(0, 105, 120, 80, 100),             # 陰,實體 100~105
              bar(1, 99, 107, 98, 106)]              # 陽,實體吞沒
        self.assertTrue(smc.engulfing(bs, 1, True))
        bs[1] = bar(1, 101, 130, 70, 104)            # 影線大,實體沒吞沒
        self.assertFalse(smc.engulfing(bs, 1, True))


class Sweep(unittest.TestCase):
    """獵取低點:影線穿過前低、收盤收回來。教學影片加的那一條。"""

    def _with_low(self):
        bs = flat(12)
        bs[2] = bar(2, 100, 100.1, 94.0, 100)        # 擺動低 94
        return bs

    def test_wick_below_and_close_above_is_a_sweep(self):
        bs = self._with_low()
        bs[8] = bar(8, 100, 100.2, 92.0, 99.0)       # 穿到 92,收在 99
        self.assertIn(8, smc.sweeps(bs))

    def test_closing_below_is_a_breakdown_not_a_sweep(self):
        """收在下面就是跌破,不是獵取。**兩者意思相反。**"""
        bs = self._with_low()
        bs[8] = bar(8, 100, 100.2, 92.0, 92.5)       # 收在 94 之下
        self.assertNotIn(8, smc.sweeps(bs))

    def test_not_reaching_the_low_is_not_a_sweep(self):
        bs = self._with_low()
        bs[8] = bar(8, 100, 100.2, 95.0, 99.0)       # 沒碰到 94
        self.assertNotIn(8, smc.sweeps(bs))

    def test_a_sweep_cannot_use_a_low_that_is_not_confirmed_yet(self):
        """只能用**當時**已確認的擺動點。偷看未來的話回測會太漂亮。"""
        bs = flat(12)
        bs[6] = bar(6, 100, 100.1, 94.0, 100)        # 擺動低在第 6 根
        bs[7] = bar(7, 100, 100.2, 92.0, 99.0)       # 第 7 根就想掃它
        # 第 6 根的擺動要到第 8 根才確認 —— 第 7 根不算獵取
        self.assertNotIn(7, smc.sweeps(bs))

    def test_requiring_a_sweep_can_only_remove_setups_never_add(self):
        """多一個前提只能讓訊號變少。變多就代表它改了別的東西。"""
        st = [Bar(T0 + timedelta(minutes=15 * i), 100, 100.2, 99.8, 100, 1.0)
              for i in range(96)]
        st[20] = Bar(st[20].t, 100, 100.2, 94.0, 100, 1.0)
        st[30] = Bar(st[30].t, 100, 100.2, 99.8, 93.0, 1.0)
        st[40] = Bar(st[40].t, 93, 97.0, 92.8, 93, 1.0)
        st[50] = Bar(st[50].t, 93, 93.2, 92.8, 92.5, 1.0)
        st[51] = Bar(st[51].t, 92.5, 98.5, 92.4, 98.0, 1.0)
        en = [Bar(T0 + timedelta(days=1, minutes=5 * i),
                  95, 95.1, 94.9, 95, 1.0) for i in range(288)]
        loose = smc.setups(st, en, both_sides=True)
        tight = smc.setups(st, en, both_sides=True, require_sweep=True)
        self.assertLessEqual(len(tight), len(loose))


class Liquidity(unittest.TestCase):
    def test_prev_day_only_uses_closed_days(self):
        d1 = [Bar(datetime(2026, 9, 1, h, tzinfo=timezone.utc),
                  100, 100 + h, 90, 100) for h in range(24)]
        d2 = [Bar(datetime(2026, 9, 2, h, tzinfo=timezone.utc),
                  100, 500, 50, 100) for h in range(5)]
        hi, lo = smc.prev_period_extremes(d1 + d2,
                                          datetime(2026, 9, 2, 5,
                                                   tzinfo=timezone.utc))
        self.assertEqual(hi, 123)        # 9/1 的高,不是 9/2 的 500
        self.assertEqual(lo, 90)


class Ladder(unittest.TestCase):
    """四步驟接起來。不求跑出獲利,只求**該有訊號時有、不該有時沒有**。"""

    def _series(self):
        # 結構層 15m:先跌破做出 down 趨勢,再收破擺動高 → CHOCH
        st = [Bar(T0 + timedelta(minutes=15 * i), 100, 100.2, 99.8, 100, 1.0)
              for i in range(96)]                    # 9/1 一整天
        st[20] = Bar(st[20].t, 100, 100.2, 94.0, 100, 1.0)
        st[30] = Bar(st[30].t, 100, 100.2, 99.8, 93.0, 1.0)
        st[40] = Bar(st[40].t, 93, 97.0, 92.8, 93, 1.0)
        st[50] = Bar(st[50].t, 93, 93.2, 92.8, 92.5, 1.0)   # 陰 K → 訂單塊
        st[51] = Bar(st[51].t, 92.5, 98.5, 92.4, 98.0, 1.0)  # 收破 97 → CHOCH
        return st

    def test_no_setup_without_fvg_or_engulf(self):
        st = self._series()
        en = [Bar(T0 + timedelta(days=1, minutes=5 * i),
                  95, 95.1, 94.9, 95, 1.0) for i in range(288)]
        self.assertEqual(smc.setups(st, en), [])

    def test_shorts_are_off_unless_asked(self):
        st = self._series()
        en = [Bar(T0 + timedelta(days=1, minutes=5 * i),
                  95, 95.1, 94.9, 95, 1.0) for i in range(288)]
        self.assertTrue(all(s.up for s in smc.setups(st, en, both_sides=True)))

    def test_entry_bar_is_after_structure_bar_closes(self):
        """不准用還沒收完的結構棒下單。"""
        st = self._series()
        en = [Bar(T0 + timedelta(minutes=5 * i), 95, 95.1, 94.9, 95, 1.0)
              for i in range(300)]
        for s in smc.setups(st, en, both_sides=True):
            self.assertGreaterEqual(
                en[s.i].t.timestamp(),
                st[51].t.timestamp() + 15 * 60)


class EventSim(unittest.TestCase):
    def test_same_bar_hits_both_counts_as_stop(self):
        """棒內順序看不到 → 一律算停損。樂觀假設會讓固定 R:R 憑空變好看。"""
        bs = [bar(0, 100, 100, 100, 100),
              bar(1, 100, 100, 100, 100),
              bar(2, 100, 120, 80, 100)]             # 同一根同時碰到
        o = run(bs, [Setup(0, True, 90.0, 110.0, 2)], cost_pct=0.0)
        self.assertEqual(o.trades[0].reason, "sl")
        self.assertAlmostEqual(o.trades[0].r, -1.0, places=6)

    def test_gap_past_stop_after_entry_fills_at_open_not_at_stop(self):
        """持倉中跳空穿過停損 → 成交在開盤價,**比 -1R 更糟**。

        首版直接用停損價結算,等於假裝崩盤時還搶得到停損價。
        那會讓每一段急跌都被少算,而且不會報錯。
        """
        bs = [bar(0, 100, 100, 100, 100),
              bar(1, 100, 100, 100, 100),            # 進場
              bar(2, 80, 81, 79, 80)]                # 跳空,開盤就在停損之下
        o = run(bs, [Setup(0, True, 90.0, 110.0, 2)], cost_pct=0.0)
        self.assertEqual(o.trades[0].reason, "sl")
        self.assertAlmostEqual(o.trades[0].r, -2.0, places=6)

    def test_entry_gap_to_wrong_side_of_stop_is_voided_and_counted(self):
        """訊號作廢要**數出來**,不能靜靜丟掉。"""
        bs = [bar(0, 100, 100, 100, 100),
              bar(1, 80, 100, 79, 85)]               # 開盤價已在停損之下
        o = run(bs, [Setup(0, True, 90.0, 110.0, 1)], cost_pct=0.0)
        self.assertEqual(o.trades, [])
        self.assertEqual(o.skipped.get("跳空作廢或資料不足"), 1)

    def test_cost_is_charged_in_r_units(self):
        bs = [bar(0, 100, 100, 100, 100),
              bar(1, 100, 100, 100, 100),
              bar(2, 100, 115, 100, 110)]
        free = run(bs, [Setup(0, True, 90.0, 110.0, 2)], cost_pct=0.0)
        paid = run(bs, [Setup(0, True, 90.0, 110.0, 2)], cost_pct=0.2)
        self.assertAlmostEqual(free.trades[0].r, 1.0, places=6)
        self.assertLess(paid.trades[0].r, free.trades[0].r)

    def test_one_position_at_a_time(self):
        bs = flat(40, 100.0)
        many = [Setup(i, True, 99.0, 101.0, 39) for i in range(0, 30)]
        o = run(bs, many, cost_pct=0.0)
        for a, b in zip(o.trades, o.trades[1:]):
            self.assertGreater(b.i_in, a.i_out)

    def test_stats_empty_is_not_a_crash(self):
        self.assertEqual(stats(run(flat(5), [], cost_pct=0.0))["n"], 0)

    def test_bootstrap_refuses_tiny_samples(self):
        """樣本太少時回 None,而不是給一個看起來很像結論的數字。"""
        self.assertIsNone(bootstrap_positive([0.5] * 5))
        self.assertIsNotNone(bootstrap_positive([0.5] * 40))


if __name__ == "__main__":
    unittest.main()


class CostDiagnostic(unittest.TestCase):
    """平均 R 為負的時候,要分得出「型態沒用」和「停損比成本還窄」。

    2026-09-25 的實跑:L1 3387 筆、平均 -1.1068R、回撤 102.8%。
    平均比 -1R 還糟,而停損就是 1R —— 那個差額只能從成本來。
    表面數字跟「型態完全沒有預測力」長得一模一樣,但兩者要做的事
    完全不同,所以這個分辨要量出來,不是用講的。
    """

    def _one(self, sl, cost):
        bs = [bar(0, 100, 100, 100, 100),
              bar(1, 100, 100, 100, 100),
              bar(2, 100, 100.5, sl - 1, 100)]
        return run(bs, [Setup(0, True, sl, 200.0, 2)], cost_pct=cost)

    def test_a_tight_stop_makes_cost_exceed_a_whole_r(self):
        wide = stats(self._one(90.0, 0.2))      # 停損 10%
        tight = stats(self._one(99.9, 0.2))     # 停損 0.1%
        self.assertLess(wide["cost_r_med"], 0.1)
        self.assertGreater(tight["cost_r_med"], 1.0)

    def test_gross_and_net_differ_by_exactly_the_cost(self):
        st = stats(self._one(99.9, 0.2))
        self.assertAlmostEqual(st["gross_r"] - st["expectancy_r"],
                               st["cost_r_med"], places=3)

    def test_gross_equals_net_when_costs_are_zero(self):
        st = stats(self._one(90.0, 0.0))
        self.assertEqual(st["cost_r_med"], 0.0)
        self.assertAlmostEqual(st["gross_r"], st["expectancy_r"], places=6)


class ControlIsAnchored(unittest.TestCase):
    """隨機對照組要先證明**它自己是對的**,才能拿它下結論。

    2026-09-25:我拿對照組的淨值 p95 下了一個結論(「SMC 進場比隨機
    丟飛鏢還糟」),而那個結論**不安全** —— 實跑顯示六套階梯扣成本前
    全部是正的。淨值的比較把「型態準不準」跟「成本結構」混在一起。

    這組測試釘的是對照組的定錨點:在**零漂移隨機漫步、零成本**上,
    隨機進場的毛利期望值必須接近 0。不接近 0 就代表對照組本身在
    製造訊號,那它量到的任何東西都不算數。
    """

    def _walk(self, n=4000, seed=3):
        import random
        rng = random.Random(seed)
        t0, px, out = T0, 100.0, []
        for i in range(n):
            o = px
            px *= (1 + rng.gauss(0.0, 0.004))
            h = max(o, px) * (1 + abs(rng.gauss(0, 0.002)))
            lo = min(o, px) * (1 - abs(rng.gauss(0, 0.002)))
            out.append(Bar(t0 + timedelta(minutes=5 * i), o, h, lo, px, 1e5))
        return out

    def test_random_entries_have_no_gross_edge_on_a_driftless_walk(self):
        import random
        from portfolio.event_sim import control
        bars = self._walk()
        rng = random.Random(11)
        ss = []
        for _ in range(300):
            i = rng.randrange(10, len(bars) - 60)
            px = bars[i].c
            ss.append(Setup(i, True, px * 0.99, px * 1.02,
                            min(i + 50, len(bars) - 1)))
        c = control(bars, ss, reps=40, cost_pct=0.0)
        self.assertLess(abs(c["median_gross"]), 0.25,
                        f"對照組自己就有優勢,它量到的東西不算數:{c}")

    def test_control_reports_gross_as_well_as_net(self):
        """只給淨值的話,看的人會拿它回答兩個不同的問題。"""
        import random
        from portfolio.event_sim import control
        bars = self._walk()
        rng = random.Random(5)
        ss = [Setup(i, True, bars[i].c * 0.99, bars[i].c * 1.02,
                    min(i + 50, len(bars) - 1))
              for i in (rng.randrange(10, len(bars) - 60) for _ in range(120))]
        c = control(bars, ss, reps=20, cost_pct=0.2)
        for k in ("median_r", "p95_r", "median_gross", "p95_gross"):
            self.assertIn(k, c)
        # 有成本的話,淨值必定低於毛利
        self.assertLess(c["median_r"], c["median_gross"])
