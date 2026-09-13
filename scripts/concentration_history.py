"""
相關性集中度的歷史分布 —— 用來決定第六十條的門檻 · 2026-09-13

═══ 為什麼需要這一支 ═══
`max_equivalent_exposure_pct` 現在是 None。要給它一個數字,有兩種方法:

  一、憑第一原則挑一個(「單幣上限是 20%,所以等效曝險也該 20%」)
  二、看這條策略在 3.3 年裡實際跑出過什麼數字,再決定

**第一種是錯的。** 單幣上限 20% 那個數字,是在假設「七個倉是七個
不同的賭注」的前提下訂的。把它直接套到等效曝險上,等於用一個
建立在錯誤前提上的數字,去修正那個錯誤前提 —— 而且它會立刻把
系統整個停掉(現在的等效曝險大約 44%)。

第二種才對,而且它有一個很重要的性質:

  **只要門檻設在歷史最大值之上,它在已知歷史上一次都不會觸發
  —— 所以 Calmar 1.33 不需要重新驗證。**

它擋的不是策略平常的樣子,是「有事情變了」。這正是一個
circuit breaker 該做的事,而不是一條天天都在管事的規則。

═══ 一件必須先講的事:相關性已經在部位大小裡了 ═══
`vol_target` 算的是**七幣等權籃子**的已實現波動(rules.py 一律傳
全部 7 個幣進去),而籃子的報酬序列本身就含相關性 ——
相關性升高 → 籃子波動升高 → 縮放係數變小 → 自動減碼。

所以這條門檻**不是**用來控制「相關性造成的波動」,那件事已經有人做了。
它控制的是另一件事:**同時被清算的風險**。2026-09-10 那次是六個倉
一起貼在強平線上 —— 那不是波動問題,波動目標對它無能為力。

門檻要照這個目的訂,不是照「分散度好不好看」訂。

═══ 用法 ═══
    python scripts/concentration_history.py
    python scripts/concentration_history.py --lookback 60 --json

輸出:等效單一標的曝險與有效檔數的歷史分布(中位數 / 90 / 95 / 99
百分位 / 最大值),以及一個建議門檻。

**它只讀資料,不寫任何帳本、不下任何單。**
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio import correlation
from portfolio.paper import MAIN, _fresh_bars
from portfolio.rules import registry, vol_target


def percentile(values: list, q: float) -> float:
    """線性插值百分位。不用 numpy —— 這個專案沒有那個相依。"""
    if not values:
        raise ValueError("沒有資料")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def walk(cfg=MAIN, lookback: int | None = None) -> dict:
    """
    重跑一次歷史,每一天算一次集中度。

    **只用 dates[:i+1]**(第三十四條)—— 這裡算的是「那一天當下
    看得到的相關性」,不是事後回頭看的。用全期相關性去回答
    「當天該不該擋」是看未來,那個門檻會訂得太鬆。
    """
    lookback = lookback or cfg.vol_lookback
    syms = list(cfg.symbols)
    dates, idx = _fresh_bars(syms, cfg.lookback_days)
    if len(dates) < 2:
        raise SystemExit("歷史資料不足 —— 先跑 scripts/daily.py 補 K 線")

    fn = vol_target(registry(syms)[cfg.strategy], cfg.vol_target_pct,
                    cfg.vol_lookback, syms, cfg.leverage_cap)

    rows, unmeasured_days = [], 0
    for i in range(len(dates) - 1):
        weights = fn(i, dates, idx) or {}
        if not weights:
            continue                      # 空手的日子沒有集中度可言

        returns = correlation.daily_returns(idx, syms, dates, i, lookback)
        c = correlation.concentration(weights, returns)
        if not c.measured:
            unmeasured_days += 1
            continue

        rows.append({
            "date": dates[i].date().isoformat(),
            "equivalent_pct": c.equivalent_exposure_pct,
            "total_pct": c.total_exposure_pct,
            "effective": c.effective_positions,
            "positions": c.positions,
        })

    if not rows:
        raise SystemExit("一天都算不出來 —— 檢查 K 線快取是不是空的")

    equivalents = [r["equivalent_pct"] for r in rows]
    effectives = [r["effective"] for r in rows]
    worst = max(rows, key=lambda r: r["equivalent_pct"])

    return {
        "days_measured": len(rows),
        "days_unmeasured": unmeasured_days,
        "window": f"{rows[0]['date']} ~ {rows[-1]['date']}",
        "lookback": lookback,
        "equivalent_pct": {
            "median": percentile(equivalents, 0.50),
            "p90": percentile(equivalents, 0.90),
            "p95": percentile(equivalents, 0.95),
            "p99": percentile(equivalents, 0.99),
            "max": max(equivalents),
        },
        "effective_positions": {
            "median": percentile(effectives, 0.50),
            "min": min(effectives),
        },
        "worst_day": worst,
        "rows": rows,
    }


def suggest(stats: dict) -> dict:
    """
    建議門檻 = 歷史最大值往上取整到 5 的倍數,再加 10 個百分點。

    為什麼是「最大值之上」而不是 p99:
      p99 會在 3.3 年裡觸發約 8 次。**那八次都發生過,而且策略
      活下來了** —— 用一個歷史上會擋掉自己八次的門檻,等於宣稱
      那八天的決定是錯的,而回測說它們沒有錯。那需要重新回測。

      設在最大值之上,已知歷史上一次都不會觸發,Calmar 1.33
      原封不動 —— 而它仍然擋得住「比歷史上任何一天都更集中」
      的新狀態。

    加 10 個百分點的緩衝:歷史最大值是一個樣本,不是上界。
    貼著它設,會在第一次遇到「稍微更極端一點但性質相同」的日子
    就停機。
    """
    ceiling = stats["equivalent_pct"]["max"]
    rounded = -(-ceiling // 5) * 5          # 向上取整到 5 的倍數
    return {
        "max_equivalent_exposure_pct": rounded + 10,
        "historical_max": ceiling,
        "why": (f"歷史最大 {ceiling:.1f}% -> 取整 {rounded:.0f}% + 緩衝 10 "
                f"= {rounded + 10:.0f}%。已知歷史上不會觸發,"
                "所以不需要重新回測。"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="相關性集中度的歷史分布")
    ap.add_argument("--lookback", type=int, default=None,
                    help=f"相關性回看天數(預設 {MAIN.vol_lookback},"
                         "與波動目標同一個窗口)")
    ap.add_argument("--json", action="store_true", help="輸出 JSON")
    args = ap.parse_args()

    stats = walk(lookback=args.lookback)
    pick = suggest(stats)

    if args.json:
        print(json.dumps({**stats, "suggestion": pick},
                         ensure_ascii=False, indent=2))
        return 0

    eq = stats["equivalent_pct"]
    ef = stats["effective_positions"]
    worst = stats["worst_day"]

    print(f"\n區間 {stats['window']}  ·  {stats['days_measured']} 個有部位"
          f"且算得出來的交易日")
    if stats["days_unmeasured"]:
        print(f"({stats['days_unmeasured']} 天有部位但相關性算不出來 —— "
              "多半是回看窗還沒填滿)")
    print(f"相關性回看 {stats['lookback']} 日\n")

    print("等效單一標的曝險")
    print(f"  中位數 {eq['median']:6.1f}%")
    print(f"  p90    {eq['p90']:6.1f}%")
    print(f"  p95    {eq['p95']:6.1f}%")
    print(f"  p99    {eq['p99']:6.1f}%")
    print(f"  最大   {eq['max']:6.1f}%")
    print()
    print(f"有效持倉檔數  中位數 {ef['median']:.2f} 檔"
          f" · 最低 {ef['min']:.2f} 檔")
    print()
    print(f"最集中的一天  {worst['date']}:"
          f"帳面 {worst['positions']} 檔 / {worst['total_pct']:.1f}%,"
          f"等效 {worst['equivalent_pct']:.1f}%,"
          f"有效 {worst['effective']:.2f} 檔")
    print()
    print("─" * 58)
    print(f"建議 max_equivalent_exposure_pct = "
          f"{pick['max_equivalent_exposure_pct']:.0f}")
    print(f"  {pick['why']}")
    print()
    print("要套用的話,把這個數字寫進 data/risk_limits.json:")
    print(f'  "max_equivalent_exposure_pct": '
          f'{pick["max_equivalent_exposure_pct"]:.0f}')
    print("這是一條 Risk Limit,依第 102 條由執政官決定,程式不自行更動。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
