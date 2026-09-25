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
