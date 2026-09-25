"""第二批假說(B1~B5)· 2026-09-25

這批測試守的是同一件事:**缺資料時要不持有,不是放行。**

第一批 13 個假說全部是收盤價的函數,彼此高度相關,所以「一個都沒過」
比較接近「一個東西沒過」。第二批問的是五件不同的事,而它們各自
需要價格以外的資料(量能、資金費率、基準幣)—— 那就多了一個
新的出錯方式:**資料缺了,而規則默默當成通過。**

那種錯不會報錯,而且會讓假說看起來比實際更好。所以每一條都在
這裡釘一個「沒資料 → 不持有」的測試。
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from portfolio import rules
from portfolio.sim import Bar

T0 = datetime(2021, 1, 1, tzinfo=timezone.utc)
SYMS = ["BTC-USDT", "ETH-USDT"]


def series(n=400, up=True, vol=1e6, syms=SYMS):
    dates = [T0 + timedelta(days=i) for i in range(n)]
    idx = {}
    for s in syms:
        px, d = 100.0, {}
        for t in dates:
            px *= 1.004 if up else 0.996
            d[t] = Bar(t, px, px * 1.01, px * 0.99, px, vol)
        idx[s] = d
    return dates, idx


def held(fn, dates, idx, i):
    return set(fn(i, dates, idx))


class VolumeConfirm(unittest.TestCase):
    def test_zero_volume_means_not_held_not_passed_through(self):
        """量能欄位 2026-09-10 才加,舊快取是 0。**0 不等於通過。**"""
        dates, idx = series(vol=0.0)
        base = rules.ma_filter(SYMS, 50)
        f = rules.volume_confirm(base)
        self.assertTrue(held(base, dates, idx, 300), "基準本來有持倉")
        self.assertEqual(held(f, dates, idx, 300), set())

    def test_above_average_volume_passes(self):
        dates, idx = series()
        for s in SYMS:                      # 最後一天爆量
            b = idx[s][dates[300]]
            idx[s][dates[300]] = Bar(b.t, b.o, b.h, b.l, b.c, 9e6)
        f = rules.volume_confirm(rules.ma_filter(SYMS, 50))
        self.assertEqual(held(f, dates, idx, 300), set(SYMS))


class FundingFilter(unittest.TestCase):
    def _fund(self, dates, rate, syms=SYMS):
        return {s: [(int(t.timestamp() * 1000), rate) for t in dates]
                for s in syms}

    def test_missing_funding_means_not_held(self):
        """沒有費率資料的幣**不持有**,不假設它是 0。"""
        dates, idx = series()
        f = rules.funding_filter(rules.ma_filter(SYMS, 50), {})
        self.assertEqual(held(f, dates, idx, 300), set())

    def test_positive_funding_is_excluded(self):
        dates, idx = series()
        f = rules.funding_filter(rules.ma_filter(SYMS, 50),
                                 self._fund(dates, 0.0002))
        self.assertEqual(held(f, dates, idx, 300), set())

    def test_negative_funding_is_kept(self):
        dates, idx = series()
        f = rules.funding_filter(rules.ma_filter(SYMS, 50),
                                 self._fund(dates, -0.0002))
        self.assertEqual(held(f, dates, idx, 300), set(SYMS))


class RelativeStrength(unittest.TestCase):
    def test_laggards_are_dropped_and_the_bench_is_kept(self):
        dates = [T0 + timedelta(days=i) for i in range(400)]
        idx = {}
        for s, g in (("BTC-USDT", 1.004), ("ETH-USDT", 1.001)):
            px, d = 100.0, {}
            for t in dates:
                px *= g
                d[t] = Bar(t, px, px * 1.01, px * 0.99, px, 1e6)
            idx[s] = d
        f = rules.relative_strength(rules.ma_filter(SYMS, 50),
                                    "BTC-USDT", 50)
        self.assertEqual(held(f, dates, idx, 300), {"BTC-USDT"})

    def test_missing_bench_means_hold_nothing(self):
        """基準算不出來 → 全部不持有,**不是全部放行**。"""
        dates, idx = series()
        f = rules.relative_strength(rules.ma_filter(SYMS, 50),
                                    "DOGE-USDT", 50)
        self.assertEqual(held(f, dates, idx, 300), set())


class Chandelier(unittest.TestCase):
    def test_a_sharp_drop_exits_even_though_the_ma_still_says_hold(self):
        """吊燈只動出場。它要能在均線還沒跌破時先出場,否則它沒有作用。"""
        dates, idx = series()
        base = rules.ma_filter(SYMS, 50)
        f = rules.chandelier(base)
        for i in range(60, 300):
            f(i, dates, idx)
        s = "BTC-USDT"
        b = idx[s][dates[300]]
        # 一天摔 8%。刻意挑在兩條線中間:3×ATR 約 6%,而 50 日均線
        # 在價格下方約 9%(每天漲 0.4% 的序列)。所以這一跌
        # **踩到吊燈但還沒跌破均線** —— 兩者才分得出來。
        drop = b.c * 0.92
        idx[s][dates[300]] = Bar(b.t, b.o, b.h, drop * 0.99, drop, b.v)
        self.assertIn(s, held(base, dates, idx, 300), "均線還沒跌破")
        self.assertNotIn(s, held(f, dates, idx, 300), "吊燈應該先出場")

    def test_state_does_not_leak_between_date_windows(self):
        """同一個物件跑第二段日期時要重置 —— 不然驗證段不乾淨。

        hunt.py 已經改成每段各建一個,這條是第二道防線。
        """
        dates, idx = series()
        f = rules.chandelier(rules.ma_filter(SYMS, 50))
        a = [held(f, dates, idx, i) for i in range(200, 260)]
        b = [held(f, dates, idx, i) for i in range(200, 260)]
        self.assertEqual(a, b, "重跑同一段應該得到一樣的結果")


class LowVol(unittest.TestCase):
    def test_threshold_only_sees_the_past(self):
        """門檻是「到當日為止」的中位數。用全段的就是拿未來的資訊。

        做法:把序列後半段的波動放大。如果門檻偷看了未來,前半段
        的低波動日會因為「跟後面的大波動比」而幾乎全部通過;
        只看過去的話,前半段是跟自己比,通過率會接近一半。
        """
        import random
        random.seed(7)
        dates = [T0 + timedelta(days=i) for i in range(600)]
        idx, s = {}, "BTC-USDT"
        px, d = 100.0, {}
        for j, t in enumerate(dates):
            sd = 0.004 if j < 300 else 0.06
            px *= (1 + random.gauss(0.002, sd))
            d[t] = Bar(t, px, px * 1.01, px * 0.99, px, 1e6)
        idx[s] = d
        f = rules.low_vol_only(rules.hold_all([s]))
        early = sum(bool(f(i, dates, idx)) for i in range(150, 300))
        self.assertLess(early, 145,
                        "前半段幾乎全通過 = 門檻偷看了後半段的大波動")


if __name__ == "__main__":
    unittest.main()
