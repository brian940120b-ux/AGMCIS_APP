"""
支撐壓力突破 · 2026-09-18

執政官問:「不是有就是說利用支撐或壓力、突破假突破、回測去判斷嗎?」

守四件事,而第一件是這類策略最貴的一種錯:

一、**不准偷看今天的高點** —— 拿今天的高點判斷今天有沒有突破今天的
    高點,那永遠成立,而回測會漂亮得不像話,**且不會報錯**
二、**用收盤不用插針** —— 那是最便宜的假突破過濾,不花參數
三、狀態不准跨回測殘留
四、資料有缺口就不算,不補
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio.rules import donchian_breakout, registry


@dataclass
class B:
    t: datetime
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0


def series(bars):
    """bars = [(high, low, close)] → (dates, idx)"""
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    dates = [t0 + timedelta(days=i) for i in range(len(bars))]
    idx = {"X": {dates[i]: B(dates[i], c, h, l, c)
                 for i, (h, l, c) in enumerate(bars)}}
    return dates, idx


def flat(n, close=100.0, high=101.0, low=99.0):
    return [(high, low, close)] * n


# ══════════════════════════════════════════════════════════
# 一、前視偏誤
# ══════════════════════════════════════════════════════════
def test_todays_own_high_is_not_part_of_the_resistance():
    """**拿今天的高點判斷今天有沒有突破今天的高點,那永遠成立。**

    這是突破類策略最常見的前視偏誤,而它不會報錯 ——
    它只會讓回測漂亮得不像話。
    """
    # 前 20 天都在 100,第 21 天收 105(它自己的高點是 106)
    bars = flat(20) + [(106.0, 99.0, 105.0)]
    dates, idx = series(bars)
    fn = donchian_breakout(["X"], entry_n=20, exit_n=10)

    for i in range(len(dates) - 1):
        fn(i, dates, idx)
    got = fn(len(dates) - 1, dates, idx)

    # 壓力 = 前 20 天的最高 = 101(不是今天的 106)
    assert got == {"X": 1.0}, "收 105 > 前 20 日高 101,該進場"


def test_a_close_below_the_prior_high_does_not_enter():
    bars = flat(20) + [(120.0, 99.0, 100.5)]        # 插針到 120,收 100.5
    dates, idx = series(bars)
    fn = donchian_breakout(["X"], entry_n=20, exit_n=10)
    for i in range(len(dates) - 1):
        fn(i, dates, idx)
    assert fn(len(dates) - 1, dates, idx) == {}, \
        "**插針穿過去再收回來不算突破** —— 這是最便宜的假突破過濾"


# ══════════════════════════════════════════════════════════
# 二、進出場
# ══════════════════════════════════════════════════════════
def run(bars, **kw):
    dates, idx = series(bars)
    fn = donchian_breakout(["X"], **kw)
    return [bool(fn(i, dates, idx)) for i in range(len(dates))]


def test_it_holds_until_the_exit_channel_breaks():
    bars = (flat(20)
            + [(106.0, 104.0, 105.0)]           # 突破,進場
            + flat(5, close=104.0, high=105.0, low=103.0)   # 抱著
            + [(104.0, 90.0, 91.0)])            # 跌破 10 日低,出場
    out = run(bars, entry_n=20, exit_n=10)
    assert out[20] is True
    assert all(out[21:26]), "沒跌破出場線之前要抱著"
    assert out[-1] is False


def test_confirmation_bars_delay_entry():
    """confirm_bars 要花一個參數,所以預設是 1 —— 但它要能動。"""
    bars = flat(20) + [(106.0, 104.0, 105.0)] + [(107.0, 105.0, 106.0)]
    assert run(bars, entry_n=20, exit_n=10, confirm_bars=1)[20] is True
    assert run(bars, entry_n=20, exit_n=10, confirm_bars=2)[20] is False, \
        "要連續兩天站穩才算"
    assert run(bars, entry_n=20, exit_n=10, confirm_bars=2)[21] is True


def test_confirmation_means_holding_one_line_not_making_new_highs():
    """**「站穩」是那條線沒被收回來,不是連續 K 天都創新高。**

    第一版拿每一天各自的壓力去比,把條件變成後者 —— 那是一個罕見
    得多的東西,而它會讓突破策略幾乎不進場,看起來像「很保守」,
    實際上是算錯了。
    """
    # 第 21 天收 105.5:站在壓力(101)之上,但**沒有**創新高(106)
    bars = flat(20) + [(106.0, 104.0, 105.0)] + [(105.6, 105.0, 105.5)]
    assert run(bars, entry_n=20, exit_n=10, confirm_bars=2)[21] is True


def test_a_close_back_below_the_line_resets_the_confirmation():
    bars = flat(20) + [(106.0, 104.0, 105.0)] + [(105.0, 99.0, 100.0)]
    assert run(bars, entry_n=20, exit_n=10, confirm_bars=2)[21] is False


# ══════════════════════════════════════════════════════════
# 三、狀態不准跨回測殘留
# ══════════════════════════════════════════════════════════
def test_state_is_cleared_when_the_backtest_restarts():
    """同一個 fn 跑第二次回測,上一次的持倉不能漏進來。

    漏進來的話第二次回測會從「已經持有」開始,而那個持倉
    **沒有任何一筆訊號支持它**。
    """
    bars = flat(20) + [(106.0, 104.0, 105.0)]
    dates, idx = series(bars)
    fn = donchian_breakout(["X"], entry_n=20, exit_n=10)
    for i in range(len(dates)):
        fn(i, dates, idx)
    assert fn(0, dates, idx) == {}, "重跑第 0 天不該還持有"


# ══════════════════════════════════════════════════════════
# 四、資料缺口
# ══════════════════════════════════════════════════════════
def test_a_gap_in_the_window_means_no_signal_not_a_patched_one():
    """中間缺一天就不算 —— **不補**。

    補出來的極值是編的,而它會變成一條沒有人下過的訂單。
    """
    bars = flat(20) + [(106.0, 104.0, 105.0)]
    dates, idx = series(bars)
    del idx["X"][dates[5]]                       # 挖一個洞
    fn = donchian_breakout(["X"], entry_n=20, exit_n=10)
    for i in range(len(dates) - 1):
        fn(i, dates, idx)
    assert fn(len(dates) - 1, dates, idx) == {}


def test_not_enough_history_means_no_signal():
    assert run(flat(5) + [(200.0, 99.0, 199.0)], entry_n=20,
               exit_n=10)[-1] is False


# ══════════════════════════════════════════════════════════
# 五、它進得了策略清單,而且值是教科書的
# ══════════════════════════════════════════════════════════
def test_the_breakout_rules_are_in_the_registry_with_textbook_values():
    """20/10 與 55/20 是海龜系統一與系統二 —— **不是搜出來的**。"""
    names = set(registry(["X"]))
    assert "突破20日高(10日低出場)" in names
    assert "突破55日高(20日低出場)" in names


def test_bad_parameters_are_refused_loudly():
    for kw in ({"entry_n": 1}, {"exit_n": 0}, {"confirm_bars": 0}):
        with pytest.raises(ValueError):
            donchian_breakout(["X"], **kw)
