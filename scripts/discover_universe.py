"""
U 本位標準合約能交易哪些幣 —— 第三版,而前兩版都錯了 · 2026-09-13

═══ 先把兩次錯誤留在這裡 ═══
`contract/v1` **沒有 contracts 端點**,所以「有哪些幣可以交易」只能
繞著問。我繞了兩次,兩次都給出一個聽起來很確定的錯答案。

**第一版**:拿「已知存在」與「已知不存在」比**回傳筆數**。

    FLOCKUSDT  回 12 筆    ZZZZZUSDT  回 49 筆
    ✓ 兩者回應不同 —— 分辨得出

12 ≠ 49,判別式說「分辨得出」,然後對 14 個候選全部印「存在」。
真相:`ZZZZZUSDT` 是我編的,它回了 49 筆 —— 交易所認不得 symbol
的時候不套用過濾。比筆數當然不同,而那個不同不是我以為的意思。

**第二版**:改成比訂單編號的集合(這一步是對的),但同時多做了
一件事 —— 從那 49 筆裡讀 `symbol` 欄位,並宣稱

    「這是交易所自己列出來的,不是推論」

而它印出來的是:

    裡面有 1 個不重複的標的:ZZZZZUSDT       49 筆
    策略現役七幣裡訂單史上沒出現過的:… BTCUSDT …

**`BTCUSDT` 在同一份輸出裡回了 4 筆有成交史。** 兩行互相打臉,
而腳本沒有發現。真相:回應的 `symbol` 欄位是**把請求的參數抄回來**,
不是那筆訂單真正的標的。所以那 49 筆的 symbol 欄位一個字都不能信。

═══ 為什麼寫下來 ═══
同一個形狀第七、第八次:**一個聽起來很確定、但其實沒有被完整問過
的答案。** 第二版更糟的地方在於,它的兩行輸出當場互相矛盾 ——
資訊就在螢幕上,而判別式沒有去看。

所以這一版多了一件事:**自我打臉檢查**。任何一個候選有成交史、
卻沒出現在「倒出來那本」的標的清單裡,就代表 symbol 欄位不可信,
腳本必須當場說出來,而不是繼續往下推論。

═══ 這一版做什麼 ═══
一、用一個編出來的代號釣出「基準集合」(認不得 → 不過濾 → 整本)
二、逐個候選,比**訂單編號的集合**:
      == 基準 → 認不得        ⊂ 基準 → 認得,過濾生效
三、**歸屬**:把所有候選問到的訂單編號聯集起來,對照基準。
      對不上的那些 = **有我們沒猜到的標的在交易**
四、對不上的那些用**成交價**去比對日線快取,把它們認出來
五、講清楚這一支到不了哪裡

**它只讀。** ReadOnlyClient 沒有 post / delete。

用法:  .venv/bin/python scripts/discover_universe.py
       .venv/bin/python scripts/discover_universe.py SOLUSDT BNBUSDT
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import interpreter

interpreter.require()

from core.config import load_env
from exchange.bingx.private import (READ_ONLY, CredentialsMissing,
                                    PrivateCallFailed, ReadOnlyClient)
from portfolio.paper import SYMBOLS

LINE = "═" * 62

BAIT = "ZZZZZUSDT"      # 一定不存在。拿它釣基準集合。

FROM_SCREENSHOT = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "LINKUSDT", "LTCUSDT",
                   "BCHUSDT", "ETCUSDT", "ALGOUSDT", "DYDXUSDT", "TRXUSDT"]


def ask(client, symbol: str):
    try:
        data = client.get(READ_ONLY["std_orders"], {"symbol": symbol})
    except PrivateCallFailed:
        return None
    return data if isinstance(data, list) else None


def ids(rows) -> frozenset:
    """訂單編號的集合。**比集合,不比筆數**(第一版的錯)。"""
    return frozenset(
        str(r.get("orderId")) for r in (rows or [])
        if isinstance(r, dict) and r.get("orderId") is not None)


def identify_by_price(order: dict) -> list:
    """拿成交價與時間去日線快取裡找,這筆訂單可能是哪個幣。

    ⚠️ **這是推論,不是事實。** 兩個幣的價位可能在某一天重疊,
    而快取只涵蓋策略的七個幣 —— 找不到不代表不是幣,
    只代表不是**這七個**。
    """
    from portfolio.sim import load_daily

    price = order.get("avgPrice") or order.get("closePrice")
    stamp = order.get("time")
    try:
        price = float(price)
        day = datetime.fromtimestamp(int(stamp) / 1000, timezone.utc).date()
    except (TypeError, ValueError):
        return []
    if price <= 0:
        return []

    out = []
    for sym in SYMBOLS:
        for bar in load_daily(sym):
            if bar.t.date() != day:
                continue
            if bar.l <= price <= bar.h:
                out.append(sym)
            break
    return out


def main(argv) -> int:
    load_env()
    try:
        client = ReadOnlyClient()
    except CredentialsMissing as e:
        print(f"✗ {e}")
        return 2

    # ── 一、基準集合 ──────────────────────────────────
    print(f"\n{LINE}\n  一、基準:認不得的代號會回整本(不過濾)\n{LINE}\n")
    baseline = ask(client, BAIT)
    if baseline is None:
        print(f"  {BAIT} 問不到 —— 整套判別式建立在「認不得就回整本」上,"
              "那個前提不成立了。先看交易所到底回了什麼,不要往下讀。")
        return 1

    base_ids = ids(baseline)
    print(f"  用 {BAIT}(我編的)問 → {len(baseline)} 筆,"
          f"{len(base_ids)} 個不重複訂單編號。")

    echoed = {r.get("symbol") for r in baseline if isinstance(r, dict)}
    symbol_field_trustworthy = echoed != {BAIT}
    if not symbol_field_trustworthy:
        print(f"\n  ⚠️ 這 {len(baseline)} 筆的 symbol 欄位**全部是 "
              f"{BAIT}** —— 那是我請求裡的參數。")
        print("     交易所把請求的 symbol 抄回每一列,而不是回那筆訂單")
        print("     真正的標的。**所以 symbol 欄位一個字都不能信**,")
        print("     這一版不從它推論任何東西(第二版就是栽在這裡)。")

    # ── 二、逐個候選 ──────────────────────────────────
    strategy = {s.replace("-", "") for s in SYMBOLS}
    candidates = list(argv) if argv else sorted(strategy
                                                | set(FROM_SCREENSHOT))
    print(f"\n{LINE}\n  二、逐個問 —— 比訂單編號的集合\n{LINE}\n")
    print(f"  {'代號':<12}{'筆數':>6}  判讀")

    recognised, unknown, unclear = [], [], []
    attributed: dict = {}
    for sym in candidates:
        rows = ask(client, sym)
        if rows is None:
            unclear.append(sym)
            print(f"  {sym:<12}{'—':>6}  問不到(錯誤)")
            continue
        got = ids(rows)
        if base_ids and got == base_ids:
            unknown.append(sym)
            verdict = "**認不得** —— 回的就是整本,過濾沒生效"
        elif got <= base_ids:
            recognised.append(sym)
            for oid in got:
                attributed[oid] = sym
            verdict = ("認得,有成交史" if got
                       else "認得,無成交史(過濾生效,結果為空)")
        else:
            unclear.append(sym)
            verdict = "?? 回了基準以外的訂單 —— 前提不成立,別解讀"
        tag = " ← 策略現役" if sym in strategy else ""
        print(f"  {sym:<12}{len(rows):>6}  {verdict}{tag}")

    # ── 自我打臉檢查(第二版缺的就是這個)──────────────
    if symbol_field_trustworthy:
        named = {r.get("symbol") for r in baseline if isinstance(r, dict)}
        contradiction = [s for s in recognised
                         if s not in named
                         and any(v == s for v in attributed.values())]
        if contradiction:
            print(f"\n  ⚠️ **自我矛盾**:{', '.join(contradiction)} 有成交史,")
            print("     卻沒出現在倒出來那本的 symbol 清單裡。")
            print("     → symbol 欄位不可信,不要從它推論任何東西。")

    # ── 三、歸屬:還有多少訂單認不出主人 ────────────────
    print(f"\n{LINE}\n  三、歸屬 —— 那本帳裡還有多少筆不屬於任何候選\n{LINE}\n")
    orphan = [r for r in baseline
              if str(r.get("orderId")) not in attributed]
    print(f"  基準 {len(base_ids)} 筆,候選認領了 {len(attributed)} 筆,"
          f"**剩 {len(orphan)} 筆沒有主人**。")
    if orphan:
        print("\n  → 這個帳戶交易過**我們沒猜到的標的**。那不是壞消息:")
        print("    它證明這個產品上不只我們列的這些幣。")
        print("\n  用成交價去日線快取裡認(⚠️ 推論,而且只涵蓋策略七幣):\n")
        guessed: dict = {}
        for row in orphan[:40]:
            hits = identify_by_price(row)
            for h in hits:
                guessed[h] = guessed.get(h, 0) + 1
        if guessed:
            for sym, n in sorted(guessed.items(), key=lambda kv: -kv[1]):
                print(f"    {sym:<12}價位對得上 {n} 筆")
            print("\n    價位對得上**不等於就是它** —— 不同幣的價位"
                  "可能在某一天重疊。這只是線索。")
        else:
            print("    七個幣的價位都對不上 —— 那些訂單是別的標的,")
            print("    而快取裡沒有它們的日線。")

    # ── 四、結論 ──────────────────────────────────────
    print(f"\n{LINE}\n  四、結論\n{LINE}\n")
    if unknown:
        print(f"  **認不得**:{', '.join(unknown)}")
    else:
        print(f"  {len(recognised)} 個候選全部被認得(過濾都生效了),")
        print("  包含策略現役的七個幣。")
    if unclear:
        print(f"\n  判讀不了:{', '.join(unclear)}")

    blocked = sorted(strategy & set(unknown))
    if blocked:
        print(f"\n  ❗ 策略現役七幣裡有 {len(blocked)} 個不能交易:"
              f"{', '.join(blocked)}")
        print("     交易池要改,而那是**策略層的決定**(§6)。")
        print("     交易池一改,回測的 Calmar 1.33 就不是這個池子的數字。")

    print(f"\n{LINE}")
    print("""  這一支到不了的地方 —— **這段不要跳過**

  「allOrders 認得這個代號」與「這個產品可以交易它」**不是同一件事**。
  allOrders 很可能拿全交易所的代號表做驗證,而不是這個產品的清單。
  所以上面的「認得」是一個**必要條件,不是充分條件**。

  這個產品沒有 contracts 端點,完整名單只能在 App 上翻。
  在翻完之前,SOL / BNB / AAVE / UNI 能不能在 U 本位標準合約上交易,
  **仍然是未知的**。這一支不會假裝它知道 —— 前兩版就是這樣錯的。""")
    print(LINE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
