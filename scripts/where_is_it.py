"""
那個倉到底在哪裡 —— 兩個環境一起查 · 2026-09-13

═══ 為什麼需要這一支 ═══
執政官在 App 上開了一個倉,而 Demo 的永續與標準合約**都查不到**。

而 Demo 餘額是 100,000 VST、**已用保證金 0**、權益 = 餘額 ——
如果那個倉真的在 Demo 上,已用保證金不可能是 0。

剩下最可能的解釋是:**那個倉開在真實帳戶上。**

那是一件要馬上知道的事,而不是慢慢猜的事。

═══ 這支做什麼 ═══
同一把金鑰,對 **Demo 與實盤兩個主機**各做一次唯讀查詢,
把餘額與持倉並排列出來。

**它只會讀。** 用的是 ReadOnlyClient —— 那個類別沒有下單的方法,
不是被擋住,是根本沒寫。連到實盤也一樣。

═══ 為什麼查實盤是安全的 ═══
· 唯讀:ReadOnlyClient 沒有 post / delete
· 金鑰:權限只勾了讀取,連下單都被交易所擋(code=100004 證明過)
· 提款:永遠 OFF

而如果那裡真的有一個用真錢開的倉,**現在知道遠比之後知道好**。

用法:  .venv/bin/python scripts/where_is_it.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import interpreter

interpreter.require()

from core import ratelimit
from core.config import load_env
from exchange.bingx import private


def look(mode: str) -> dict:
    """一個環境的快照。任何一項失敗都照實記下來,不讓它中斷另一項。"""
    out = {"mode": mode, "host": private.host(mode)}
    try:
        client = private.ReadOnlyClient(mode=mode)
    except Exception as e:
        out["error"] = str(e)
        return out

    for label, call in (
            ("balance", client.balance),
            ("perp", client.positions),
            ("standard", client.standard_positions),
            ("open_orders", client.open_orders)):
        try:
            out[label] = call()
        except (private.PrivateCallFailed, ratelimit.RateLimited) as e:
            out[label] = f"查不到:{e}"
    return out


def summarise_balance(data):
    row = data
    if isinstance(row, dict):
        row = row.get("balance") or row
    if isinstance(row, list):
        row = row[0] if row else {}
    if not isinstance(row, dict):
        return str(data)[:60]

    def num(*names):
        for n in names:
            if row.get(n) is not None:
                try:
                    return float(row[n])
                except (TypeError, ValueError):
                    return None
        return None

    asset = row.get("asset") or "?"
    equity = num("equity")
    used = num("usedMargin", "positionMargin")
    return (f"{asset}  權益 {equity if equity is None else f'{equity:,.2f}'}"
            f"  已用保證金 {used if used is None else f'{used:,.4f}'}")


def count(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        value = value.get("orders") or value.get("positions") or []
    return f"{len(value or [])} 筆"


def main() -> int:
    load_env()

    try:
        private.Credentials.from_env()
    except private.CredentialsMissing as e:
        print(f"\n{e}\n")
        return 2

    print()
    print("═" * 62)
    print("  同一把金鑰,兩個環境 —— 只讀,不下單")
    print("═" * 62)

    found_live = False
    for mode in ("demo", "live"):
        snap = look(mode)
        print()
        print(f"  【{'Demo(VST 虛擬)' if mode == 'demo' else '實盤(真錢)'}】"
              f"  {snap['host']}")
        if "error" in snap:
            print(f"    連不上:{snap['error']}")
            continue
        print(f"    餘額      {summarise_balance(snap['balance'])}")
        print(f"    永續持倉  {count(snap['perp'])}")
        print(f"    標準持倉  {count(snap['standard'])}")
        print(f"    掛單      {count(snap['open_orders'])}")

        rows = snap["perp"]
        if isinstance(rows, list) and rows:
            if mode == "live":
                found_live = True
            print()
            print("    交易所實際回的欄位名:")
            for key in sorted(rows[0]):
                print(f"      {key} = {rows[0][key]}")

    print()
    print("═" * 62)
    if found_live:
        print()
        print("  ⚠️  **實盤有持倉 —— 那是真錢。**")
        print()
        print("      這支只會讀,不會動它。要處理的話請在 App 上手動平倉,")
        print("      或先確認那是不是你本來就有的倉。")
        print()
        print("      程式這邊不受影響:BINGX_ENV=demo,而且 LIVE_ENABLED")
        print("      是 False —— 下單層連建立都會失敗。")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
