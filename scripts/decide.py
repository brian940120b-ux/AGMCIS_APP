"""
裁決一個提案 —— 而裁決**不等於生效** · 2026-09-13

═══ 為什麼裁決與生效是兩步 ═══
「執政官說可以」與「參數真的改了」是兩件事,而它們在稽核上
必須分得開:

  · 裁決留下的是**決定**:誰、什麼時候、根據哪一份證據
  · 生效留下的是**改動**:哪一個常數從什麼變成什麼

合成一步的話,事後只看得到「參數是這個」,看不到「誰決定的」。
而風控參數被誰改過,是這個系統最需要看得見的事情之一(第 102 條)。

═══ 這一支做什麼 ═══
只改 data/proposals.json 裡那一筆的狀態。**不碰任何設定檔、
不碰任何常數。** 核可之後它會把「要改哪一行」印出來給人自己改 ——
那一步刻意留給人,因為改風控參數不該是一個腳本的副作用。

用法:
    .venv/bin/python scripts/decide.py                    # 列出待裁決的
    .venv/bin/python scripts/decide.py <id> --accept
    .venv/bin/python scripts/decide.py <id> --reject --note "理由"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import interpreter

interpreter.require()

from portfolio import research as R

LINE = "═" * 64


def show_pending() -> int:
    items = R.pending()
    if not items:
        print("\n  沒有待裁決的提案。\n")
        print("  產生提案:.venv/bin/python scripts/research.py\n")
        return 0
    print(f"\n{LINE}\n  待裁決 {len(items)} 件\n{LINE}\n")
    for p in items:
        c = p.challenger
        print(f"  {p.proposal_id}   {p.created_utc[:10]}")
        print(f"    現任 → {c.get('ma')} 日均線 · "
              f"波動目標 {c.get('vol_target_pct')}% · "
              f"槓桿 {c.get('leverage_cap')}×")
        print(f"    驗證段 Calmar {p.test.get('calmar')} "
              f"vs 現任 {p.incumbent_test.get('calmar')}"
              f"(優勢 {p.calmar_edge})")
        print(f"    試過 {p.trials} 種 · 校正後 p={p.p_corrected}")
        for name, ok, detail in p.checks:
            print(f"      {'✓' if ok else '✗'} {name}:{detail}")
        print()
    print("  核可:  .venv/bin/python scripts/decide.py <id> --accept")
    print("  駁回:  .venv/bin/python scripts/decide.py <id> --reject\n")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="裁決提案(不會改任何設定)")
    ap.add_argument("proposal_id", nargs="?", help="提案編號")
    ap.add_argument("--accept", action="store_true")
    ap.add_argument("--reject", action="store_true")
    ap.add_argument("--note", default=None, help="理由,會記在提案上")
    ap.add_argument("--by", default="執政官")
    args = ap.parse_args(argv)

    if not args.proposal_id:
        return show_pending()
    if args.accept == args.reject:
        ap.error("要嘛 --accept 要嘛 --reject,選一個")

    try:
        p = R.decide(args.proposal_id,
                     "accepted" if args.accept else "rejected",
                     by=args.by, note=args.note)
    except (R.NotFound, R.AlreadyDecided, ValueError) as e:
        print(f"\n✗ {e}\n")
        return 1

    print(f"\n{LINE}\n  {p.proposal_id} → {p.status}\n{LINE}\n")
    print(f"  裁決人  {p.decided_by}")
    print(f"  時間    {p.decided_on}")
    if p.note:
        print(f"  理由    {p.note}")

    if p.status == "accepted":
        c = p.challenger
        print(f"\n  **它還沒有生效。** 要生效,改這三個常數:\n")
        print("    portfolio/paper.py")
        print(f"      VOL_TARGET_ANNUAL_PCT = {c.get('vol_target_pct')}")
        print(f"      LEVERAGE_CAP          = {c.get('leverage_cap')}")
        print(f"    均線天數 {c.get('ma')} —— 見 STRATEGY 的定義")
        print("\n  改完 commit,commit 訊息裡帶上這個提案編號 ——")
        print("  那是「這個數字為什麼是這個數字」唯一的線索。")
        print("\n  改風控參數不該是一個腳本的副作用,所以這一步留給人。")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
