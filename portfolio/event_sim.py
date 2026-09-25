"""
事件型模擬器 —— 單筆進出(進場 / 停損 / 止盈)· 2026-09-25

═══ 為什麼不能用 sim.py 跑 SMC ═══
sim.py 的判準從第一天就是「一條權益曲線比躺著不動好嗎」,因為現役
策略是**曝險型**的:它沒有單筆優勢可言,只有持有多少、什麼時候空手。
sim.py 的 docstring 寫得很清楚:硬把曝險策略塞進單筆 R 的框架會得到廢話。

**反過來一樣成立。** SMC 是**單筆賭注**型:它有明確的進場、停損、
止盈,每一筆是獨立的一次下注。硬把它塞進「每天持有多少權重」的框架,
會得到的是另一種廢話 —— 因為停損和止盈都發生在盤中,日線權重模型
根本看不到它們,結果會系統性偏樂觀。

所以這一支獨立存在。判準是**每筆的 R 倍數**,那是這類策略唯一
有意義的單位。

═══ 刻意保守的地方(全部列在這裡,不散在程式裡)═══
· 訊號在第 i 根**收盤**成立,成交在第 i+1 根的**開盤** —— 與 sim.py 同一條
· 同一根 K 棒同時碰到停損與止盈 → **一律算停損**。日線 / 分鐘線都
  看不到棒內順序,而樂觀假設會讓任何固定 R:R 的策略憑空變好看
· 跳空:成交價若已經越過停損 / 止盈,就用**開盤價**結算,不假裝
  還能在原價成交
· 一個幣同時只能有一筆單。開著的時候出現新訊號 → 丟掉,不加倉
· 成本走 costs.round_trip_pct(),不自己寫常數

═══ 隨機對照組 ═══
固定 R:R 的策略有一個陷阱:R:R 拉得夠大的話,**隨便亂進場**也會
有漂亮的單筆盈虧比(只是勝率低)。所以光看「平均 R > 0」不夠。
`control()` 用**同樣的停損 / 止盈距離、隨機的進場時點**跑一遍 ——
策略要贏的是它,不是贏「零」。這是型態類策略唯一誠實的對照。
"""
from __future__ import annotations

import random
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio.costs import round_trip_pct
from portfolio.sim import Bar

#: 每筆下注佔權益的風險比例(畫權益曲線用)。固定值,不搜。
RISK_PCT = 1.0


@dataclass(frozen=True)
class Setup:
    """一個已經成立的進場訊號。價位在建構時就算好,模擬器不再改。"""
    i: int                  # 訊號成立的 K 棒(收盤);成交在 i+1 開盤
    up: bool
    sl: float
    tp: float
    expire_i: int           # 撐到這一根還沒結束就平倉出場
    symbol: str = ""
    why: str = ""


@dataclass(frozen=True)
class Trade:
    symbol: str
    i_in: int
    i_out: int
    up: bool
    entry: float
    exit: float
    sl: float
    tp: float
    r: float                # 扣成本後的 R 倍數
    reason: str             # tp / sl / expire
    #: 這一筆的來回成本，換算成 R。停損距離越窄，同樣的手續費就吃掉
    #: 越多個 R —— 這個數字如果大於 1，代表「單是進出一趟就輸掉一個
    #: 完整的風險額度」，那時候策略準不準已經不是重點了。
    cost_r: float = 0.0


@dataclass
class Outcome:
    trades: list[Trade] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    #: 沒能成交的訊號,分類計數。**丟掉的東西要看得見** ——
    #: 靜靜丟掉就是「沒查出來」與「沒有」混為一談,那是本專案
    #: 反覆抓到的同一種錯。
    skipped: dict = field(default_factory=dict)


def _resolve(bars: list[Bar], s: Setup, cost_pct: float) -> Trade | None:
    """把一個訊號走到它的結局。回傳 None = 資料不夠,不是虧損。"""
    j = s.i + 1
    if j >= len(bars):
        return None
    entry = bars[j].o
    risk = (entry - s.sl) if s.up else (s.sl - entry)
    if risk <= 0:
        # 跳空到停損的錯邊:多單想在 100 進場,隔根開在 80,而停損在 90。
        # 這種單**實務上會撤掉**(進場價已經比停損還低),所以不成交。
        # 它被 skipped 數出來,因為它挑掉的都是最劇烈的時刻。
        return None
    cost_r = (entry * cost_pct / 100.0) / risk

    def done(k: int, px: float, why: str) -> Trade:
        raw = (px - entry) if s.up else (entry - px)
        return Trade(s.symbol, j, k, s.up, entry, px, s.sl, s.tp,
                     raw / risk - cost_r, why, cost_r)

    # 成交當根:先看有沒有一開盤就已經越過(跳空),再看這根剩下的走勢。
    if (s.up and entry <= s.sl) or (not s.up and entry >= s.sl):
        return done(j, entry, "sl")
    if (s.up and entry >= s.tp) or (not s.up and entry <= s.tp):
        return done(j, entry, "tp")
    b0 = bars[j]
    if (b0.l <= s.sl) if s.up else (b0.h >= s.sl):
        return done(j, s.sl, "sl")
    if (b0.h >= s.tp) if s.up else (b0.l <= s.tp):
        return done(j, s.tp, "tp")

    for k in range(j + 1, min(len(bars), s.expire_i + 1)):
        b = bars[k]
        # 先看開盤:跳空越過停損的話,**成交在開盤價,不在停損價**。
        # 2026-09-25 修:首版直接用 s.sl 結算,等於假裝跳空時還搶得到
        # 停損價。那是這支最貴的一種樂觀 —— 崩盤時每一筆都會少算。
        if (b.o <= s.sl) if s.up else (b.o >= s.sl):
            return done(k, b.o, "sl")
        if (b.o >= s.tp) if s.up else (b.o <= s.tp):
            return done(k, b.o, "tp")
        hit_sl = (b.l <= s.sl) if s.up else (b.h >= s.sl)
        hit_tp = (b.h >= s.tp) if s.up else (b.l <= s.tp)
        if hit_sl:                        # 同根都碰到 → 一律算停損
            return done(k, s.sl, "sl")
        if hit_tp:
            return done(k, s.tp, "tp")
    k = min(len(bars), s.expire_i + 1) - 1
    return done(k, bars[k].c, "expire")


def run(bars: list[Bar], setups: list[Setup], *,
        cost_pct: float | None = None, risk_pct: float = RISK_PCT) -> Outcome:
    """跑完一批訊號。同一時間只准有一筆單開著。"""
    cost = round_trip_pct() if cost_pct is None else cost_pct
    out = Outcome(equity=[1.0])
    busy_until = -1
    sk = out.skipped
    for s in sorted(setups, key=lambda x: x.i):
        if s.i <= busy_until:
            sk["手上有單"] = sk.get("手上有單", 0) + 1
            continue                      # 不加倉
        t = _resolve(bars, s, cost)
        if t is None:
            sk["跳空作廢或資料不足"] = sk.get("跳空作廢或資料不足", 0) + 1
            continue
        busy_until = t.i_out
        out.trades.append(t)
        out.equity.append(out.equity[-1] * (1 + t.r * risk_pct / 100.0))
    return out


def stats(o: Outcome) -> dict:
    """單筆賭注型策略的判準。**不是** sim.py 那一套。"""
    rs = [t.r for t in o.trades]
    n = len(rs)
    if not n:
        return {"n": 0}
    wins = [r for r in rs if r > 0]
    losses = [-r for r in rs if r < 0]
    peak, dd = o.equity[0], 0.0
    for e in o.equity:
        peak = max(peak, e)
        dd = max(dd, (peak - e) / peak if peak else 0.0)
    return {
        "n": n,
        "win_pct": round(100.0 * len(wins) / n, 1),
        "expectancy_r": round(statistics.fmean(rs), 4),
        "sum_r": round(sum(rs), 2),
        "profit_factor": (round(sum(wins) / sum(losses), 2)
                          if losses and sum(losses) else None),
        "equity_pct": round(100.0 * (o.equity[-1] - 1.0), 2),
        "max_dd_pct": round(100.0 * dd, 1),
        "tp_pct": round(100.0 * sum(t.reason == "tp" for t in o.trades) / n, 1),
        # 成本佔掉幾個 R(中位數)。**平均 R 為負的時候先看這一格。**
        # 大於 1 就代表停損設得比來回成本還窄 —— 那是結構問題,
        # 不是「這個型態沒有預測力」。兩者的處理方式完全不同。
        "cost_r_med": round(statistics.median([t.cost_r for t in o.trades]), 3),
        "gross_r": round(statistics.fmean(
            [t.r + t.cost_r for t in o.trades]), 4),
    }


def bootstrap_positive(rs: list[float], *, n: int = 4000,
                       seed: int = 20260925) -> float | None:
    """單尾自助法:重抽之後平均 R <= 0 的比例,就是 p。

    重抽的是**交易**不是日報酬 —— 這類策略的樣本單位是一筆交易。
    """
    if len(rs) < 20:
        return None                       # 樣本太少,p 值沒有意義
    rng = random.Random(seed)
    m = len(rs)
    bad = sum(1 for _ in range(n)
              if statistics.fmean(rng.choices(rs, k=m)) <= 0)
    return round(bad / n, 4)


def control(bars: list[Bar], setups: list[Setup], *, reps: int = 200,
            seed: int = 20260925, cost_pct: float | None = None) -> dict:
    """隨機對照組:同樣的停損 / 止盈距離,進場時點打散。

    回傳對照組平均 R 的分布(**扣成本後與扣成本前都給**)。
    如果策略的平均 R 落在對照組分布裡面,那它量到的不是型態,
    是那組 R:R 本身。

    ═══ 2026-09-25:為什麼扣成本前也要給 ═══
    首版只回扣成本後的數字,而我拿它下了一個**不安全的結論**
    (「SMC 進場比隨機丟飛鏢還糟」)。問題在於:淨值的比較把
    「型態準不準」跟「成本結構」混在一起,而實跑顯示六套階梯
    **扣成本前全部是正的**(+0.05 ~ +0.14R)。

    要回答「型態有沒有預測力」,該比的是**毛利**;
    要回答「這樣做會不會賺錢」,才比淨利。兩個問題不一樣,
    而只給一個數字的話,看的人一定會拿它回答兩個。
    """
    if not setups or len(bars) < 10:
        return {"reps": 0}
    cost = round_trip_pct() if cost_pct is None else cost_pct
    rng = random.Random(seed)
    span = [s.expire_i - s.i for s in setups]
    means: list[float] = []
    gross: list[float] = []
    for _ in range(reps):
        fake: list[Setup] = []
        for s, hold in zip(setups, span):
            i = rng.randrange(1, max(2, len(bars) - 2))
            px = bars[i].c
            # 保持同樣的相對距離,換一個進場點
            d_sl = abs(bars[s.i].c - s.sl) / max(bars[s.i].c, 1e-9)
            d_tp = abs(s.tp - bars[s.i].c) / max(bars[s.i].c, 1e-9)
            sl = px * (1 - d_sl) if s.up else px * (1 + d_sl)
            tp = px * (1 + d_tp) if s.up else px * (1 - d_tp)
            fake.append(Setup(i, s.up, sl, tp,
                              min(i + max(1, hold), len(bars) - 1)))
        ts = run(bars, fake, cost_pct=cost).trades
        if ts:
            means.append(statistics.fmean([t.r for t in ts]))
            gross.append(statistics.fmean([t.r + t.cost_r for t in ts]))
    if not means:
        return {"reps": 0}
    means.sort()
    gross.sort()

    def _p95(v):
        return round(v[int(0.95 * (len(v) - 1))], 4)

    return {"reps": len(means),
            "median_r": round(statistics.median(means), 4),
            "p95_r": _p95(means),
            "median_gross": round(statistics.median(gross), 4),
            "p95_gross": _p95(gross)}


def beats_control(expectancy_r: float, ctrl: dict) -> bool:
    """策略要贏的是對照組的第 95 百分位,不是贏「零」。"""
    p95 = ctrl.get("p95_r")
    return p95 is not None and expectancy_r > p95
