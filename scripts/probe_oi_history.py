"""
探針:測量官到底錄了多少 OI · 2026-09-25

執政官傳來一個「OI／持倉異常監控面板」的畫面,問它能不能用。

═══ 在回答之前要先知道的一件事 ═══
那個面板做的是**即時警報**:掃市場、發現異常、推卡片。這個系統的
規矩是**沒有回測就不准上場**,而回測要的是**歷史**,不是即時值。

所以「OI 有沒有用」這個問題,第一步不是寫面板,是問:
**我手上有多少 OI 歷史?**

而這裡有一個容易被忽略的事實:`scripts/gauge.py`(測量官)從很早
以前就每 15 分鐘把全池的 funding / open_interest / mark_price 寫進
`data/gauge_history.jsonl`,而它 docstring 寫的目的就是
「供未來假說做無前視回測」,還先把驗收條件寫下來了:
**「證據錄滿一兩個月,才有資格上法庭。」**

這支就是去數那個「一兩個月」到了沒有。

═══ 這支不下判斷 ═══
跟其他 probe_*.py 同一條規矩:**只呈現證據,不給結論。**
2026-09-18 我把一個參數錯誤讀成「端點不存在」,又把一個網址讀成
「這是永續的」。兩次都是拿一個看起來像答案的東西當答案。

所以這支只數數:幾筆、幾天、幾個幣、每個幣多少個觀測、有沒有斷。
夠不夠、能不能做成假說,留給看得到全貌的人。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = Path(__file__).resolve().parents[1]
HIST = BASE / "data" / "gauge_history.jsonl"
LINE = "═" * 62


def main() -> int:
    print(f"\n{LINE}\n  測量官歷史:{HIST}\n{LINE}\n")
    if not HIST.exists():
        print("  檔案不存在 —— 測量官沒有跑過,或者跑在別的目錄。")
        print("  查:systemctl status agmcis-gauge.timer")
        return 0

    size = HIST.stat().st_size
    print(f"  檔案大小  {size / 1e6:.1f} MB")

    lines = bad = 0
    first = last = None
    per_sym: Counter = Counter()
    oi_present: Counter = Counter()
    fr_present: Counter = Counter()
    stamps: list[str] = []
    with HIST.open(encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            lines += 1
            try:
                snap = json.loads(raw)
            except json.JSONDecodeError:
                bad += 1
                continue
            ts = snap.get("ts")
            if ts:
                first = first or ts
                last = ts
                stamps.append(ts)
            for r in snap.get("rows") or []:
                s = r.get("symbol")
                if not s:
                    continue
                per_sym[s] += 1
                if r.get("open_interest") is not None:
                    oi_present[s] += 1
                if r.get("funding_rate") is not None:
                    fr_present[s] += 1

    print(f"  快照筆數  {lines}(壞掉的 {bad} 行)")
    print(f"  最早      {first}")
    print(f"  最晚      {last}")
    if first and last:
        try:
            d = (datetime.fromisoformat(last) - datetime.fromisoformat(first))
            print(f"  跨幅      {d.days} 天 {d.seconds // 3600} 小時")
        except ValueError as e:
            print(f"  跨幅      算不出來:{e}")

    # 有沒有斷:相鄰快照間隔遠大於 15 分鐘的次數
    gaps = 0
    biggest = 0.0
    for a, b in zip(stamps, stamps[1:]):
        try:
            dt = (datetime.fromisoformat(b)
                  - datetime.fromisoformat(a)).total_seconds() / 60
        except ValueError:
            continue
        if dt > 45:
            gaps += 1
            biggest = max(biggest, dt)
    print(f"  斷過      {gaps} 次(間隔 > 45 分鐘),最長 "
          f"{biggest / 60:.1f} 小時")

    print(f"\n  幣種      {len(per_sym)} 個")
    print(f"\n  {'幣':<16}{'快照':>8}{'有 OI':>8}{'有費率':>8}")
    for s, n in per_sym.most_common(40):
        print(f"  {s:<16}{n:>8}{oi_present[s]:>8}{fr_present[s]:>8}")
    if len(per_sym) > 40:
        print(f"  …… 還有 {len(per_sym) - 40} 個")

    print(f"\n{LINE}")
    print("  測量官 docstring 自己寫的驗收條件:")
    print("    「證據錄滿一兩個月,才有資格上法庭。」")
    print("  這支只數數,**夠不夠由上面的數字說,不由這支說。**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
