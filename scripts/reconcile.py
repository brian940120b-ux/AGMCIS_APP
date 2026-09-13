"""
對帳 —— 把交易所看到的,跟紙上帳本擺在一起 · 2026-09-13

═══ 這支會告訴你兩件事 ═══
一、**欄位形狀對不對。** account.py 自稱對齊 BingX Swap V2 的欄位名,
    那是一句宣稱,從來沒有人拿真的回應對過。
    (2026-09-13 問到的餘額端點其實是 v3,已經證明照記憶寫死很危險。)

二、**數量對不對。** 帳本說持有什麼,交易所說持有什麼。

**它只讀,不下單、不修改任何一邊的帳。** 差異是訊號,不是待辦事項。

═══ 現在跑會看到什麼 ═══
紙上帳本是 10,000 USDT 的模擬,Demo 帳戶是 100,000 VST ——
**它們本來就是兩個不同的帳戶**,所以差異一定滿江紅。那是對的。

現在真正要看的是**上半部**:欄位形狀。
持倉是 0 檔的時候,持倉欄位會顯示「未驗證」而不是「通過」——
因為沒有樣本可以對照,而「沒發現問題」不等於「沒問題」。

用法:  .venv/bin/python scripts/reconcile.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import interpreter

# 用錯直譯器的時候講人話,而不是丟一個 ModuleNotFoundError 讓人猜。
interpreter.require()

from core.config import load_env
from exchange.bingx import private
from portfolio import reconcile as rec
from portfolio.account import Account
from portfolio.paper import MAIN


def line(char="─", n=58):
    print(char * n)


def show_fields(report):
    mark = "✓" if report.ok else ("—" if not report.checked else "✗")
    print(f"  {mark} {report.what}欄位", end="")
    if not report.checked:
        print("  未驗證")
        print(f"      {report.reason}")
        return
    print(f"  {len(report.present)} 個對得上")
    for want, got in report.via_alias.items():
        print(f"      · {want} 交易所叫 {got} —— 認得,但交易所改過名字")
    for name in report.missing:
        flag = "⚠️ 關鍵" if name in report.missing_critical else "  次要"
        print(f"      {flag} 缺少 {name}")


def main() -> int:
    load_env()

    try:
        client = private.ReadOnlyClient()
    except private.CredentialsMissing as e:
        print(f"\n{e}\n")
        return 2

    try:
        balance = client.balance()
        positions = client.positions()
    except private.PrivateCallFailed as e:
        print(f"\n讀不到交易所帳戶:{e}\n")
        return 1

    book = Account.load(MAIN.state_path)
    ours = {s: p.position_amt for s, p in book.positions.items()}

    report = rec.reconcile(ours, balance, positions)

    print()
    line("═")
    print(f"  對帳 · {'實盤' if private.is_live() else 'Demo(VST)'}")
    line("═")
    print("  欄位形狀")
    show_fields(report.balance_fields)
    show_fields(report.position_fields)
    line()
    print(f"  帳本持倉  {report.ours_count} 檔")
    print(f"  交易所    {report.theirs_count} 檔")
    line()

    if not report.differences:
        print("  沒有數量差異。")
    else:
        print(f"  {len(report.differences)} 筆差異:")
        for d in report.differences:
            print(f"    · {d.symbol:<14} [{d.kind}] {d.detail}")

    line("═")
    print()

    if not report.position_fields.checked:
        print("  ⚠️  持倉欄位還沒有被驗證過 —— 需要至少一個真的倉。")
        print("      裡面有 liquidationPrice(強平價),算錯的後果不是")
        print("      數字難看,是倉沒了。第一個 Demo 倉開出來的時候,")
        print("      要再跑一次這支。")
        print()

    if report.ours_count and not report.theirs_count:
        print("  紙上帳本與 Demo 帳戶本來就是兩個不同的帳戶,")
        print("  所以持倉全部對不上是預期的。現在要看的是上面的欄位形狀。")
        print()

    return 0 if report.clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
