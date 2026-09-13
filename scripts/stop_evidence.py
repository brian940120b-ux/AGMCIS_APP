"""
後備停損擺哪裡 —— 拿歷史算,不要拿感覺猜 · 2026-09-13

═══ 這一支要回答的問題 ═══
第 102 條:Risk Limit 由執政官決定。但「決定」不等於「憑印象挑一個」——
它需要一個數字,而那個數字歷史上有答案:

    **在均線出場真的觸發之前,單一部位最深的逆向走勢是多少?**

擺得比那個淺,就會在歷史上真的發生過的**正常波動**裡被掃出去。
那不是停損,那是把策略換成另一條 —— 而 Calmar 1.33 不是它的數字。

═══ MAE:最大逆向走勢 ═══
每一段持有(進場那天到均線出場那天)算一個數字:

    MAE% = (進場價 − 持有期間最低價) / 進場價 × 100      (做多)

用**最低價**不是收盤價 —— 停損是掛在交易所的,盤中觸到就成交。
用收盤價會系統性低估,而低估的方向剛好是「停損看起來可以擺很近」。

═══ 這一支不決定任何事 ═══
它只把分布印出來。停損擺哪裡是執政官的決定,而這一支負責讓那個
決定有依據 —— 以及讓「擺 5% 會掃掉歷史上 34% 的部位」這種事
在按下去之前就看得見。

用法:  .venv/bin/python scripts/stop_evidence.py
       .venv/bin/python scripts/stop_evidence.py --ma 50 --leverage 3
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import interpreter

interpreter.require()

from portfolio.paper import SYMBOLS
from portfolio.sim import align

LINE = "═" * 62


def sma(bars, i: int, n: int):
    """第 i 根(含)往前 n 根的收盤均價。不足就回 None,不補。"""
    if i + 1 < n:
        return None
    window = bars[i + 1 - n:i + 1]
    return sum(b.c for b in window) / n


def holdings(bars, n: int) -> list:
    """走一遍「收盤在均線之上才持有」,切出每一段持有。

    ⚠️ 進場與出場都在**隔日開盤** —— 與 paper.plan() 的規則一致
    (訊號用第 i 日收盤,成交在第 i+1 日開盤)。這裡不能用收盤價
    當成交價:那會偷到一天的先機,而回測與實際就對不起來了。
    """
    out, entry_i = [], None
    for i in range(len(bars) - 1):
        ma = sma(bars, i, n)
        if ma is None:
            continue
        above = bars[i].c > ma

        if above and entry_i is None:
            entry_i = i + 1                      # 隔日開盤進場
        elif not above and entry_i is not None:
            out.append((entry_i, i + 1))         # 隔日開盤出場
            entry_i = None

    if entry_i is not None and entry_i < len(bars):
        out.append((entry_i, len(bars) - 1))     # 還在場內的那一段
    return out


def mae_of(bars, start: int, end: int) -> tuple:
    """一段持有的最大逆向走勢與最終報酬。

    MAE 用**期間最低價**,不是收盤 —— 停損掛在交易所,盤中觸到就成交。
    用收盤算會系統性低估,而低估的方向剛好讓停損看起來可以擺很近。
    """
    entry = bars[start].o
    if entry <= 0:
        return None, None
    low = min(b.l for b in bars[start:end + 1])
    exit_px = bars[end].o
    return (entry - low) / entry * 100.0, (exit_px - entry) / entry * 100.0


def pct(values: list, q: float) -> float:
    """第 q 百分位。線性插值,不四捨五入到樣本上。"""
    if not values:
        return float("nan")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ma", type=int, default=50, help="均線天數")
    ap.add_argument("--leverage", type=float, default=3.0,
                    help="要對照強平距離的槓桿")
    args = ap.parse_args(argv)

    dates, idx = align(SYMBOLS)
    if not idx:
        print("✗ 沒有日線快取。先跑:.venv/bin/python scripts/daily.py")
        return 1

    rows, all_mae = [], []
    for sym in SYMBOLS:
        bars = sorted(idx.get(sym, {}).values(), key=lambda b: b.t) \
            if sym in idx else []
        if len(bars) < args.ma + 5:
            print(f"  ⚠️ {sym} 樣本不足({len(bars)} 根),跳過")
            continue
        maes = []
        for start, end in holdings(bars, args.ma):
            mae, ret = mae_of(bars, start, end)
            if mae is None:
                continue
            maes.append(mae)
            all_mae.append((mae, ret, sym))
        if maes:
            rows.append((sym, len(maes), max(maes), pct(maes, 95),
                         pct(maes, 50)))

    if not all_mae:
        print("✗ 切不出任何一段持有 —— 樣本太短。")
        return 1

    print(f"\n{LINE}")
    print(f"  {args.ma} 日均線出場之前,單一部位最深的逆向走勢")
    print(f"  {len(all_mae)} 段持有,{len(rows)} 個標的")
    print(LINE)
    print(f"\n  {'標的':<12}{'段數':>5}{'最深':>9}{'95百分位':>11}{'中位':>9}")
    for sym, n, worst, p95, p50 in sorted(rows, key=lambda r: -r[2]):
        print(f"  {sym:<12}{n:>5}{worst:>8.1f}%{p95:>10.1f}%{p50:>8.1f}%")

    maes = [m for m, _, _ in all_mae]
    print(f"\n  全部合起來  最深 {max(maes):.1f}%"
          f"   95百分位 {pct(maes, 95):.1f}%"
          f"   中位 {pct(maes, 50):.1f}%")

    # ── 擺在不同位置會掃掉多少 ────────────────────────
    print(f"\n{LINE}")
    print("  停損擺在這裡的話,歷史上會被掃出去幾段")
    print("  「被掃掉的裡面有幾段其實是賺的」才是真正的成本")
    print(LINE)
    print(f"\n  {'停損':>6}{'掃掉':>8}{'占比':>9}{'其中賺的':>10}"
          f"{'被放棄的獲利':>14}")
    for level in (3, 5, 8, 10, 12, 15, 20, 25, 30):
        hit = [(m, r) for m, r, _ in all_mae if m >= level]
        if not hit:
            print(f"  {level:>5}%{0:>8}{0.0:>8.1f}%{'—':>10}{'—':>14}")
            continue
        winners = [r for _, r in hit if r > 0]
        forgone = sum(winners)
        print(f"  {level:>5}%{len(hit):>8}"
              f"{len(hit) / len(all_mae) * 100:>8.1f}%"
              f"{len(winners):>10}{forgone:>13.1f}%")

    # ── 跟強平距離對照 ────────────────────────────────
    from portfolio.account import MAINT_MARGIN_RATE
    liq_dist = (1.0 / args.leverage - MAINT_MARGIN_RATE) * 100.0
    print(f"\n{LINE}")
    print(f"  對照:{args.leverage:g}× 的強平距離約 {liq_dist:.1f}%")
    print(LINE)
    print("\n  停損一定要比強平**近**,否則倉會先被強平,停損永遠不觸發。")
    usable = [l for l in (3, 5, 8, 10, 12, 15, 20, 25, 30) if l < liq_dist]
    if usable:
        print(f"  {args.leverage:g}× 之下可用的範圍:{usable[0]}% ~ "
              f"{usable[-1]}%")
    else:
        print(f"  ❗ {args.leverage:g}× 之下**沒有**可用的停損位置 —— "
              "強平比任何合理的停損都近。降槓桿。")

    print(f"\n{LINE}")
    print("  怎麼讀這張表")
    print(LINE)
    print("""
  · 「最深」是歷史上真的發生過的。停損擺得比它淺,就是在說
    「我接受那一段會被掃掉」—— 那可能是對的決定,但要是刻意的。
  · 「其中賺的」是真正的代價:被掃出去的部位裡,有幾段最後其實
    是賺錢的。那些獲利是停損直接放棄掉的。
  · 這是**後備**停損,不是策略出場。它擺在策略正常運作時永遠碰不到
    的地方,只回答一個問題:這台機器死掉的時候,這個倉最多虧多少?
    所以應該往「最深」那一側靠,不是往中位數靠。
  · 這張表是歷史。**歷史沒有上限保證** —— 明天可以比最深的那一段
    更深。停損擺在歷史最深值上,不等於永遠不會被掃到。
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
