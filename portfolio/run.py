from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from portfolio.sim import align, simulate, metrics
from portfolio.rules import registry
from portfolio.judge import split_index, verdict

SYMS = ["BTC-USDT","ETH-USDT","SOL-USDT","BNB-USDT","XRP-USDT","AAVE-USDT","UNI-USDT"]

def seg(dates, idx, fn, lo, hi):
    return metrics(simulate(dates[lo:hi], idx, fn, warmup=200))

def main():
    dates, idx = align(SYMS)
    n = len(dates); cut = split_index(n)
    print(f"全段 {dates[0].date()} → {dates[-1].date()} ({n} 日)")
    print(f"訓練 {dates[0].date()} → {dates[cut-1].date()} · "
          f"驗證 {dates[cut].date()} → {dates[-1].date()}\n")
    rows = {}
    for name in registry(SYMS):
        full = metrics(simulate(dates, idx, registry(SYMS)[name], warmup=200))
        tr = seg(dates, idx, registry(SYMS)[name], 0, cut)
        te = seg(dates, idx, registry(SYMS)[name], cut - 200, n)
        rows[name] = (full, tr, te)
    hdr = f"{'策略':<22}{'總報酬':>10}{'年化':>9}{'最大回撤':>10}{'Calmar':>8}{'Sharpe':>8}{'在場%':>7}{'費用%':>7}{'資金費%':>8}"
    print(hdr); print("-"*len(hdr))
    for name,(f,_,_) in rows.items():
        print(f"{name:<22}{f['total_pct']:>9.1f}%{f['cagr_pct']:>8.1f}%"
              f"{f['max_dd_pct']:>9.1f}%{str(f.get('calmar')):>8}{f['sharpe']:>8.2f}"
              f"{f['days_in_market_pct']:>6.0f}%{f['fee_paid_pct']:>6.1f}%{f['funding_paid_pct']:>7.1f}%")
    print()
    base="買入持有(基準)"
    bf,btr,bte = rows[base]
    for name,(f,tr,te) in rows.items():
        if name==base: continue
        v = verdict(name, tr, te, btr, bte)
        print(f"  {'✓' if v['passed'] else '✗'} {name}")
        print(f"      訓練 Calmar {tr.get('calmar')} vs 基準 {btr.get('calmar')} · "
              f"驗證 {te.get('calmar')} vs {bte.get('calmar')}")
        print(f"      {v['verdict']}")
    print(f"\n  基準:訓練 Calmar {btr.get('calmar')} (回撤 {btr['max_dd_pct']}%) · "
          f"驗證 {bte.get('calmar')} (回撤 {bte['max_dd_pct']}%)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
