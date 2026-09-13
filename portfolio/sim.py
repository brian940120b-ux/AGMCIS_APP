"""
新系統 — 日線組合模擬器 · 2026-09-08

═══ 與舊回測引擎的差別,以及為什麼要另寫一支 ═══
舊引擎(backtest/engine.py)是「訊號 → 單一部位 → 停損停利 → 平倉」,
warmup 寫死 3300 根 15 分鐘 K。它為**一連串獨立賭注**設計,
判準是單筆 R —— 那套東西判不了「持有多少、什麼時候空手」。

曝險型策略沒有「單筆優勢」可言。它只有一條權益曲線,
而唯一有意義的問題是:**這條曲線比躺著不動好嗎?好在哪裡?**

所以判準從第一天就是報酬 / 最大回撤 / 對基準,不是 expectancy_r。
硬把曝險策略塞進單筆 R 的框架會得到廢話 —— 那是舊系統的錯,不重蹈。

═══ 刻意保守的地方(全部寫在這裡,不散在程式裡)═══
· 訊號用**當日收盤**算,成交在**隔日開盤** —— 不給自己看不到的資訊
· 換手一律收來回成本;持倉每天收資金費
· 不使用槓桿(權重合計 <= 1.0);槓桿是放大器不是優勢,先證明有優勢
· 資料不足的幣直接跳過,不用鄰近幣填補
"""
from __future__ import annotations

import csv
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.logging import get_logger
from portfolio.costs import measured_funding_8h_pct, round_trip_pct

log = get_logger("portfolio.sim")

BASE = Path(__file__).resolve().parents[1]
HIST = BASE / "data" / "history"


@dataclass
class Bar:
    t: datetime
    o: float
    h: float
    l: float
    c: float
    # 2026-09-10 加:成交量。有預設值,既有呼叫點(paper._fresh_bars 等)
    # 不受影響 —— 只有需要量能的訊號才會用到。
    v: float = 0.0


@dataclass
class Result:
    equity: list[float] = field(default_factory=list)
    dates: list[datetime] = field(default_factory=list)
    turnover_pct: float = 0.0
    fee_paid_pct: float = 0.0
    funding_paid_pct: float = 0.0
    days_in_market: int = 0
    rebalances: int = 0


def load_daily(symbol: str) -> list[Bar]:
    p = HIST / f"{symbol}_1d.csv"
    out: list[Bar] = []
    try:
        with p.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                try:
                    out.append(Bar(
                        datetime.fromisoformat(r["open_time"]),
                        float(r["open"]), float(r["high"]),
                        float(r["low"]), float(r["close"]),
                        float(r.get("volume") or 0.0)))
                except (KeyError, ValueError):
                    continue
    except OSError:
        return []
    out.sort(key=lambda b: b.t)
    return out


def align(symbols: list[str]) -> tuple[list[datetime], dict[str, dict]]:
    """把多個幣對齊到共同日期軸。回傳 (日期, {幣: {日期: Bar}})。"""
    series = {s: load_daily(s) for s in symbols}
    series = {s: v for s, v in series.items() if len(v) > 50}
    if not series:
        return [], {}
    idx = {s: {b.t: b for b in v} for s, v in series.items()}
    all_dates = sorted({b.t for v in series.values() for b in v})
    return all_dates, idx


def simulate(dates: list[datetime], idx: dict[str, dict],
             weights_fn, start_equity: float = 10_000.0,
             warmup: int = 200) -> Result:
    """跑一條權益曲線。

    weights_fn(i, dates, idx) -> {幣: 權重}
      · 只能看 dates[:i+1](含當日收盤),看不到未來
      · 權重合計必須 <= 1.0(不加槓桿)
    成交在**隔日開盤**,所以第 i 日決定的權重從第 i+1 日開始生效。
    """
    rt = round_trip_pct() / 100.0
    fund_day = measured_funding_8h_pct() * 3 / 100.0   # 每日資金費(做多付)
    eq = start_equity
    res = Result()
    held: dict[str, float] = {}

    for i in range(warmup, len(dates) - 1):
        today, nxt = dates[i], dates[i + 1]
        want = weights_fn(i, dates, idx) or {}
        tot = sum(want.values())
        if tot > 1.0 + 1e-9:                    # 不加槓桿,超過就等比縮回
            want = {k: v / tot for k, v in want.items()}

        # 換手成本:權重變動的絕對值總和 × 單邊成本
        turn = sum(abs(want.get(k, 0.0) - held.get(k, 0.0))
                   for k in set(want) | set(held))
        if turn > 1e-9:
            fee = eq * turn * (rt / 2.0)        # turn 已是雙邊變動量
            eq -= fee
            res.fee_paid_pct += fee / start_equity * 100
            res.turnover_pct += turn * 100
            res.rebalances += 1

        # ── 報酬一律以「隔日開盤 → 再隔日開盤」計 ────────────────
        # 2026-09-08:首版註解寫「成交在隔日開盤」,程式卻用當日收盤
        # 到隔日收盤 —— 訊號用當日收盤算,又在當日收盤成交,
        # 等於假設你看到收盤價的同一瞬間就能用它成交。
        # 那是前視偏誤,而且是註解與實作各說各話(第一次「兩把尺」)。
        # 現在:第 i 日收盤決定權重 → 第 i+1 日**開盤**成交 →
        # 報酬算到第 i+2 日開盤。訊號與成交之間永遠隔著一個可交易的間隙。
        ret = 0.0
        nxt2 = dates[i + 2] if i + 2 < len(dates) else None
        if nxt2 is None:
            break
        for sym, w in want.items():
            b1 = idx.get(sym, {}).get(nxt)
            b2 = idx.get(sym, {}).get(nxt2)
            if not b1 or not b2 or b1.o <= 0:
                continue
            ret += w * (b2.o - b1.o) / b1.o
        exposure = sum(want.values())
        fund = eq * exposure * fund_day
        eq = eq * (1 + ret) - fund
        res.funding_paid_pct += fund / start_equity * 100
        if exposure > 1e-9:
            res.days_in_market += 1

        held = want
        res.equity.append(eq)
        res.dates.append(nxt)
    return res


def metrics(res: Result, start_equity: float = 10_000.0) -> dict:
    eq = res.equity
    if len(eq) < 2:
        return {"error": "樣本不足"}
    days = max((res.dates[-1] - res.dates[0]).days, 1)
    years = days / 365.25
    total = eq[-1] / start_equity - 1
    cagr = (eq[-1] / start_equity) ** (1 / years) - 1 if years > 0 else 0.0
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak)
    rets = [(eq[i] / eq[i - 1] - 1) for i in range(1, len(eq))]
    sd = statistics.pstdev(rets) if len(rets) > 2 else 0.0
    sharpe = (statistics.mean(rets) / sd * (365 ** 0.5)) if sd > 0 else 0.0
    return {
        "days": days, "years": round(years, 2),
        "total_pct": round(total * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "max_dd_pct": round(mdd * 100, 2),
        "calmar": round(cagr / mdd, 2) if mdd > 1e-9 else None,
        "sharpe": round(sharpe, 2),
        "days_in_market_pct": round(res.days_in_market / len(eq) * 100, 1),
        "rebalances": res.rebalances,
        "fee_paid_pct": round(res.fee_paid_pct, 2),
        "funding_paid_pct": round(res.funding_paid_pct, 2),
        "final_equity": round(eq[-1], 2),
    }
