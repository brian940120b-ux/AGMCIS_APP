"""
訊號實驗室 · 2026-09-10
規格與判準見 docs/preregistration-signal-lab.md(58d3009,測前寫死)。

**這支只讀不寫帳本。** 它不碰主城、不碰測試組,只跑回測並登記結果。

═══ 三個機制 ═══
一、統一流程:所有訊號跑完全相同的資料/切分/成本/基準/判準
二、測試次數登記到 data/signal_lab.json —— 這個數字決定門檻
三、**最後 90 個交易日鎖起來**,本階段任何測試都不得使用
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio import signals as sig_lib
from portfolio.paper import (LEVERAGE_CAP, STRATEGY, SYMBOLS, VOL_LOOKBACK,
                             VOL_TARGET_ANNUAL_PCT)
from portfolio.rules import hold_all, registry, vol_target
from portfolio.sim import align, metrics, simulate

BASE = Path(__file__).resolve().parents[1]
REGISTRY = BASE / "data" / "signal_lab.json"

# ── 預先登記的切分(測前寫死,不得調整)────────────────────
HOLDOUT_DAYS = 90        # 保留段:本階段任何測試都不得觸碰
TRAIN_FRAC = 2 / 3
WARMUP = VOL_LOOKBACK + 5
# ─────────────────────────────────────────────────────────


def _run(dates, idx, weight_fn):
    return metrics(simulate(dates, idx, weight_fn, warmup=WARMUP))


def _calmar(m):
    return (m.get("calmar") or 0.0) if m else 0.0


def _dd(m):
    return (m.get("max_dd_pct") or 999.0) if m else 999.0


def main() -> int:
    dates_all, idx = align(SYMBOLS)
    if len(dates_all) < HOLDOUT_DAYS + 200:
        print("資料不足")
        return 1

    # 保留段切掉 —— 本階段完全不使用
    dates = dates_all[:-HOLDOUT_DAYS]
    split = int(len(dates) * TRAIN_FRAC)
    train, valid = dates[:split], dates[split - WARMUP - 5:]

    print("═" * 62)
    print("  訊號實驗室 · 統一測試檯")
    print("═" * 62)
    print(f"  全部資料 {len(dates_all)} 天"
          f"({dates_all[0].date()} → {dates_all[-1].date()})")
    print(f"  🔒 保留段 {HOLDOUT_DAYS} 天"
          f"({dates_all[-HOLDOUT_DAYS].date()} → {dates_all[-1].date()})"
          " —— 本階段完全不使用")
    print(f"  可用段 {len(dates)} 天 → 訓練 {len(train)} / 驗證 "
          f"{len(valid) - WARMUP - 5} 天\n")

    # 對照組一:現行策略;對照組二:買入持有
    cur_fn = vol_target(registry(SYMBOLS)[STRATEGY], VOL_TARGET_ANNUAL_PCT,
                        VOL_LOOKBACK, SYMBOLS, LEVERAGE_CAP)
    cur = {"train": _run(train, idx, cur_fn), "valid": _run(valid, idx, cur_fn)}
    bh = {"train": _run(train, idx, hold_all(SYMBOLS)),
          "valid": _run(valid, idx, hold_all(SYMBOLS))}

    print(f"{'':22s} {'訓練Calmar':>11s} {'驗證Calmar':>11s} "
          f"{'驗證回撤':>10s} {'驗證報酬':>10s}")
    print(f"{'現行策略(對照)':22s} {_calmar(cur['train']):>11.2f} "
          f"{_calmar(cur['valid']):>11.2f} {_dd(cur['valid']):>9.2f}% "
          f"{cur['valid']['total_pct']:>9.2f}%")
    print(f"{'買入持有(基準)':22s} {_calmar(bh['train']):>11.2f} "
          f"{_calmar(bh['valid']):>11.2f} {_dd(bh['valid']):>9.2f}% "
          f"{bh['valid']['total_pct']:>9.2f}%")
    print("─" * 62)

    results = []
    for name, raw in sig_lib.candidates(SYMBOLS).items():
        fn = vol_target(raw, VOL_TARGET_ANNUAL_PCT, VOL_LOOKBACK,
                        SYMBOLS, LEVERAGE_CAP)
        try:
            tr, va = _run(train, idx, fn), _run(valid, idx, fn)
        except Exception as e:
            print(f"{name:22s} ✗ {type(e).__name__}: {e}")
            continue
        k1 = _calmar(tr) > _calmar(cur["train"])
        k2 = _calmar(va) > _calmar(cur["valid"])
        k3 = _dd(va) <= _dd(cur["valid"])
        ok = k1 and k2 and k3
        mark = "✅" if ok else ("  " if not (k1 or k2) else "· ")
        print(f"{name:22s} {_calmar(tr):>11.2f} {_calmar(va):>11.2f} "
              f"{_dd(va):>9.2f}% {va['total_pct']:>9.2f}%  {mark}"
              f"{'' if ok else f' [{int(k1)}{int(k2)}{int(k3)}]'}")
        results.append({"name": name, "train": tr, "valid": va,
                        "k1": k1, "k2": k2, "k3": k3, "passed": ok})

    print("─" * 62)
    passed = [r for r in results if r["passed"]]
    n = len(results)
    print(f"  測了 {n} 個訊號,{len(passed)} 個三條全過")
    print(f"  判準旗標 [k1k2k3] = [訓練Calmar勝 驗證Calmar勝 驗證回撤不增]")
    print()
    print("  ═══ 多重檢定 ═══")
    print(f"  本輪測試次數 N = {n}。即使全部是雜訊,平均也會有"
          f" {n * 0.05:.1f} 個通過 p<0.05 的門檻。")
    if passed:
        print(f"  → {len(passed)} 個通過,但**通過本身不等於有優勢** —— "
              "候選者仍須在保留段驗證一次(且只有一次)。")
    else:
        print("  → 沒有任何訊號通過。這個結果不需要多重檢定修正:"
              "零個通過本來就低於雜訊預期。")

    # 登記(不論好壞全部寫進去)
    prev = []
    if REGISTRY.exists():
        try:
            prev = json.loads(REGISTRY.read_text(encoding="utf-8"))\
                .get("runs", [])
        except Exception:
            prev = []
    rec = {
        "t": datetime.now(timezone.utc).isoformat(),
        "preregistration": "docs/preregistration-signal-lab.md (58d3009)",
        "holdout_days": HOLDOUT_DAYS,
        "window": f"{dates[0].date()} → {dates[-1].date()}",
        "control_current": cur, "control_buyhold": bh,
        "n_tested": n, "n_passed": len(passed),
        "results": results,
    }
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(
        {"runs": prev + [rec],
         "cumulative_tests": sum(r.get("n_tested", 0) for r in prev) + n},
        ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(r.get("n_tested", 0) for r in prev) + n
    print(f"\n  已登記 → {REGISTRY.name}(累計測試次數 {total})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
