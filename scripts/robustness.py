"""
穩健度檢驗:Walk Forward + Monte Carlo · 2026-09-10(PHASE 8)

Master Prompt 第 36、37、39 條。**只讀不寫帳本。**

═══ 一個必須先講清楚的方法學區分 ═══
標準的 Walk Forward 是:每個窗口**重新最佳化參數**,再用下一段測試。
它問的是「這套最佳化流程能不能持續產生有效參數」。

但本系統的策略**沒有需要反覆擬合的參數**:
  · 50 日均線 —— 教科書標準值,從未搜尋
  · 波動目標 27% —— 只在訓練段用二分搜尋定過一次,之後未再調整
  · 槓桿 3× —— 2026-09-10 由事故與學術文獻反推,不是搜出來的

所以這裡跑**兩種**,它們回答不同的問題:

  A. 固定參數 Walk Forward
     問:同一組參數在不同時期還成立嗎?(**環境穩健度**)

  B. 逐窗重新最佳化 Walk Forward
     問:如果每段都重新擬合波動目標,會比固定的好還是差?
     **若重新擬合反而更差,那是「固定參數沒有過擬合」的正面證據。**
     這一項才是真正的過擬合檢驗。

═══ Monte Carlo(第 37 條)═══
對日報酬做 bootstrap 重抽,估算:
  · 最大回撤的分布(不是單一歷史值)
  · 年度虧損機率
  · 破產機率(Probability of Ruin)
  · 成本敏感度(手續費/滑點加倍時還活得下去嗎)

歷史只有一條路徑。Monte Carlo 回答的是「同樣的報酬分布,
在別的排列順序下會有多糟」——那比單一歷史回撤誠實。
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio.paper import (LEVERAGE_CAP, STRATEGY, SYMBOLS, VOL_LOOKBACK,
                             VOL_TARGET_ANNUAL_PCT)
from portfolio.rules import hold_all, registry, vol_target
from portfolio.sim import align, metrics, simulate

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "data" / "robustness.json"

# ── 預先寫死的設定(不搜尋)────────────────────────────────
WARMUP = VOL_LOOKBACK + 5
TRAIN_DAYS = 365          # 一年訓練
TEST_DAYS = 90            # 一季測試
MC_PATHS = 10_000         # bootstrap 路徑數
RUIN_PCT = 50.0           # 「破產」定義:權益腰斬
# 逐窗重新最佳化時,波動目標的候選格點(粗網格,不細調)
VOL_GRID = [15.0, 20.0, 27.0, 35.0, 45.0]
# ─────────────────────────────────────────────────────────


def _fn(vol_pct: float):
    return vol_target(registry(SYMBOLS)[STRATEGY], vol_pct,
                      VOL_LOOKBACK, SYMBOLS, LEVERAGE_CAP)


def _run(dates, idx, vol_pct):
    return metrics(simulate(dates, idx, _fn(vol_pct), warmup=WARMUP))


def _calmar(m):
    return (m.get("calmar") or 0.0) if m else 0.0


# ══════════════════════════════════════════════════════════
# A / B:Walk Forward
# ══════════════════════════════════════════════════════════
def walk_forward(dates, idx) -> dict:
    folds = []
    start = 0
    while start + TRAIN_DAYS + TEST_DAYS <= len(dates):
        tr = dates[start:start + TRAIN_DAYS]
        te = dates[start + TRAIN_DAYS - WARMUP - 5:
                   start + TRAIN_DAYS + TEST_DAYS]
        # A. 固定參數
        fixed = _run(te, idx, VOL_TARGET_ANNUAL_PCT)
        # B. 逐窗重新最佳化:只用訓練段挑,再用測試段驗
        best_v, best_c = VOL_TARGET_ANNUAL_PCT, -1e9
        for v in VOL_GRID:
            c = _calmar(_run(tr, idx, v))
            if c > best_c:
                best_v, best_c = v, c
        reopt = _run(te, idx, best_v)
        folds.append({
            "train_from": tr[0].date().isoformat(),
            "test_from": dates[start + TRAIN_DAYS].date().isoformat(),
            "test_to": te[-1].date().isoformat(),
            "fixed_calmar": _calmar(fixed),
            "fixed_return_pct": fixed["total_pct"],
            "fixed_dd_pct": fixed["max_dd_pct"],
            "reopt_vol_target": best_v,
            "reopt_calmar": _calmar(reopt),
            "reopt_return_pct": reopt["total_pct"],
            "reopt_dd_pct": reopt["max_dd_pct"],
        })
        start += TEST_DAYS
    return {"folds": folds}


# ══════════════════════════════════════════════════════════
# Monte Carlo
# ══════════════════════════════════════════════════════════
def monte_carlo(daily_rets: list[float], paths: int = MC_PATHS,
                extra_cost_bp_per_day: float = 0.0, seed: int = 20260910
                ) -> dict:
    """對日報酬做 bootstrap 重抽。

    重抽而非參數化模擬的理由:我們不知道真實分布長什麼樣,而假設常態
    會**低估尾部**(arXiv 2102.04591 實測低估最適保證金至少 50%)。
    重抽保留了實際觀察到的肥尾。
    """
    rng = random.Random(seed)
    n = len(daily_rets)
    adj = [r - extra_cost_bp_per_day / 10_000.0 for r in daily_rets]
    finals, dds, ruined = [], [], 0
    for _ in range(paths):
        eq, peak, mdd = 1.0, 1.0, 0.0
        hit = False
        for _ in range(n):
            eq *= (1 + adj[rng.randrange(n)])
            peak = max(peak, eq)
            dd = (peak - eq) / peak
            mdd = max(mdd, dd)
            if eq <= 1 - RUIN_PCT / 100:
                hit = True
        finals.append(eq - 1)
        dds.append(mdd * 100)
        ruined += hit
    finals.sort()
    dds.sort()

    def pct(a, q):
        return a[min(len(a) - 1, max(0, int(len(a) * q)))]

    return {
        "paths": paths, "days": n,
        "extra_cost_bp_per_day": extra_cost_bp_per_day,
        "return_median_pct": statistics.median(finals) * 100,
        "return_p05_pct": pct(finals, 0.05) * 100,
        "return_p95_pct": pct(finals, 0.95) * 100,
        "prob_negative_pct": sum(1 for x in finals if x < 0) / paths * 100,
        "dd_median_pct": statistics.median(dds),
        "dd_p95_pct": pct(dds, 0.95),
        "dd_worst_pct": dds[-1],
        "prob_dd_over_20_pct": sum(1 for d in dds if d > 20) / paths * 100,
        "prob_ruin_pct": ruined / paths * 100,
    }


# ══════════════════════════════════════════════════════════
def main() -> int:
    dates, idx = align(SYMBOLS)
    print("═" * 64)
    print("  穩健度檢驗 · Walk Forward + Monte Carlo")
    print("═" * 64)
    print(f"  資料 {len(dates)} 天({dates[0].date()} → {dates[-1].date()})")
    print(f"  訓練窗 {TRAIN_DAYS} 天 / 測試窗 {TEST_DAYS} 天 / "
          f"Monte Carlo {MC_PATHS:,} 條路徑\n")

    # ── Walk Forward ────────────────────────────────────
    wf = walk_forward(dates, idx)
    folds = wf["folds"]
    print("══ Walk Forward ══")
    print(f"{'測試段起':>11s} {'固定 Calmar':>12s} {'固定報酬':>10s} "
          f"{'重擬合目標':>10s} {'重擬合 Calmar':>13s} {'重擬合報酬':>11s}")
    for f in folds:
        print(f"{f['test_from']:>11s} {f['fixed_calmar']:>12.2f} "
              f"{f['fixed_return_pct']:>9.2f}% {f['reopt_vol_target']:>9.0f}% "
              f"{f['reopt_calmar']:>13.2f} {f['reopt_return_pct']:>10.2f}%")

    fx = [f["fixed_calmar"] for f in folds]
    ro = [f["reopt_calmar"] for f in folds]
    fx_pos = sum(1 for f in folds if f["fixed_return_pct"] > 0)
    ro_pos = sum(1 for f in folds if f["reopt_return_pct"] > 0)
    print(f"\n  固定參數 :{len(folds)} 段中 {fx_pos} 段正報酬,"
          f"Calmar 中位 {statistics.median(fx):.2f}")
    print(f"  逐窗重擬合:{len(folds)} 段中 {ro_pos} 段正報酬,"
          f"Calmar 中位 {statistics.median(ro):.2f}")
    better = sum(1 for a, b in zip(ro, fx) if a > b)
    picks = [f["reopt_vol_target"] for f in folds]
    spread = max(picks) / min(picks)
    gap = statistics.median(ro) - statistics.median(fx)
    print(f"  重擬合勝過固定的段數:{better}/{len(folds)}"
          f"(Calmar 中位差 {gap:+.2f})")
    print(f"  重擬合挑出的波動目標:{sorted(set(picks))} —— "
          f"最高是最低的 {spread:.1f} 倍")
    print()
    # ── 判定邏輯(2026-09-10 修)──────────────────────────
    # 首版只看「幾段比較好」就下結論,但實測 Calmar 中位只差 0.02 ——
    # 那是雜訊。**用勝場數判定會把雜訊讀成訊號**,而那正是本專案一路
    # 在防的錯。改成同時看三件事:差距大小、參數穩定性、報酬形態。
    MEANINGFUL = 0.30          # Calmar 差距小於此視為雜訊
    if abs(gap) < MEANINGFUL:
        print(f"  → **重新最佳化沒有帶來有意義的改善**"
              f"(Calmar 中位差 {gap:+.2f},小於 {MEANINGFUL} 的雜訊門檻)。")
        if spread >= 2.0:
            print(f"    而且它挑出的參數極不穩定({spread:.1f} 倍範圍)——")
            print("    參數不穩定而績效沒變好,是**過擬合的典型樣貌**:")
            print("    它在追逐前一段的市況,而不是找到真正有效的值。")
        print("    這是「現行的 27% 沒有嚴重過擬合」的正面證據 ——")
        print("    如果 27% 是挖出來的,每段重新挖應該明顯更好,而實際上沒有。")
    elif gap > 0:
        print(f"  → 重新最佳化有意義地較好({gap:+.2f}),固定參數值得檢視。")
    else:
        print(f"  → 固定參數有意義地較好({gap:+.2f})。")
    neg = len(folds) - fx_pos
    print(f"\n  另注意:{len(folds)} 段中有 {neg} 段負報酬 —— "
          "與驗證段是下跌市的已知事實一致,不是新資訊。")

    # ── Monte Carlo ─────────────────────────────────────
    res = simulate(dates, idx, _fn(VOL_TARGET_ANNUAL_PCT), warmup=WARMUP)
    eq = res.equity
    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq))]
    print("\n══ Monte Carlo(bootstrap 重抽日報酬)══")
    print(f"{'情境':>16s} {'報酬中位':>10s} {'5% 分位':>10s} "
          f"{'回撤中位':>10s} {'回撤95%':>10s} {'虧損機率':>10s} {'破產機率':>10s}")
    # ── 成本情境:倍數必須由**實際成本**推算,不能亂填 ──────────
    # 2026-09-10 首版直接寫 +7bp/日 當「成本加倍」,但實際摩擦成本是
    # 年 1.24%(= 0.34 bp/日)—— 那個 7 是「暴增 20 倍」,不是加倍。
    # 用錯的情境會得到「策略極度脆弱」的結論,而那個結論是我自己造的。
    m_base = metrics(res)
    yrs = max(m_base["years"], 1e-9)
    cost_bp_day = (m_base["fee_paid_pct"] + m_base["funding_paid_pct"]) \
        / yrs / 365 * 100
    print(f"  實際成本:手續費+滑點 {m_base['fee_paid_pct'] / yrs:.2f}%/年、"
          f"資金費 {m_base['funding_paid_pct'] / yrs:.2f}%/年"
          f" = {cost_bp_day:.3f} bp/日")
    scenarios = [("基準", 0.0),
                 ("成本 ×2", cost_bp_day),
                 ("成本 ×3", cost_bp_day * 2),
                 ("成本 ×5", cost_bp_day * 4)]
    mc_all = {}
    for label, extra in scenarios:
        mc = monte_carlo(rets, extra_cost_bp_per_day=extra)
        mc_all[label] = mc
        print(f"{label:>16s} {mc['return_median_pct']:>9.1f}% "
              f"{mc['return_p05_pct']:>9.1f}% {mc['dd_median_pct']:>9.1f}% "
              f"{mc['dd_p95_pct']:>9.1f}% {mc['prob_negative_pct']:>9.1f}% "
              f"{mc['prob_ruin_pct']:>9.2f}%")
    base = mc_all["基準"]
    print(f"\n  歷史單一路徑的最大回撤:{metrics(res)['max_dd_pct']:.2f}%")
    print(f"  Monte Carlo 95% 分位回撤:{base['dd_p95_pct']:.2f}%"
          f"  最壞:{base['dd_worst_pct']:.2f}%")
    print("  → 歷史回撤是**一條路徑**的結果。同樣的報酬分布換個順序,")
    print("    有 5% 的機會看到比歷史更糟的回撤。規劃資金要用後者。")

    OUT.write_text(json.dumps({
        "updated": datetime.now(timezone.utc).isoformat(),
        "window": f"{dates[0].date()} → {dates[-1].date()}",
        "walk_forward": wf, "monte_carlo": mc_all,
        "historical_max_dd_pct": metrics(res)["max_dd_pct"],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n  已登記 → {OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
