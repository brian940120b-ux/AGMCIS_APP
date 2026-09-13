"""
U 本位標準合約能交易哪些幣 —— 從那份「洩漏出來的全帳」讀 · 2026-09-13

═══ 上一版錯在哪裡(2026-09-13,一小時內)═══
上一版拿「已知存在」與「已知不存在」比對,而比的是**回傳筆數**:

    已知存在   FLOCKUSDT  回 12 筆
    已知不存在 ZZZZZUSDT  回 49 筆
    ✓ 兩者回應不同 —— 分辨得出

12 ≠ 49,所以判別式說「分辨得出」。**那句話是錯的**,而且錯得比
「分辨不出」更糟:它給了一個看起來確定的答案。

真相是:`ZZZZZUSDT` 這個我自己編出來的代號回了 **49 筆訂單** ——
交易所在認不得 symbol 的時候,**直接把整個帳戶的訂單史倒出來**,
不套用任何過濾。比筆數當然「不同」,而那個不同完全不是我以為的
那個意思。

**同一個形狀,第七次:一個聽起來很確定、但其實沒有被完整問過的答案。**
這次的差別是對照組本來就抓到了 —— 是我的判別式把它丟掉的。

═══ 但那個 bug 送了我們一份禮物 ═══
既然認不得的 symbol 會回**整本訂單史**,那本帳裡的 `symbol` 欄位
就是**這個產品上真的被交易過的標的**。那不是推論,是交易所自己
列出來的。

所以這一版反過來做:
  一、故意用一個認不得的代號,把整本訂單史要出來
  二、讀出裡面所有不重複的 symbol —— **這是硬事實**
  三、再逐個候選問,判別式改成比**訂單編號的集合**:
      回的內容跟「整本」一樣 = 這個代號沒被認得
      回的內容是「整本」的子集 = 被認得,而且過濾生效了

═══ 它還是不能回答什麼 ═══
「被交易過」不等於「可交易的完整名單」——一個上架但從沒下過單的
標的不會出現在訂單史裡。這個產品**沒有 contracts 端點**,
完整名單只能在 App 上翻。這一支不會假裝它有。

**它只讀。** ReadOnlyClient 沒有 post / delete。

用法:  .venv/bin/python scripts/discover_universe.py
       .venv/bin/python scripts/discover_universe.py SOLUSDT BNBUSDT
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import interpreter

interpreter.require()

from core.config import load_env
from exchange.bingx.private import (READ_ONLY, CredentialsMissing,
                                    PrivateCallFailed, ReadOnlyClient)
from portfolio.paper import SYMBOLS

LINE = "═" * 62

# 一個一定不存在的代號。用它把整本訂單史釣出來 ——
# 上一版把這個行為當成 bug,這一版拿它當工具。
BAIT = "ZZZZZUSDT"

# App 截圖上看得到的(可能不完整)
FROM_SCREENSHOT = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "LINKUSDT", "LTCUSDT",
                   "BCHUSDT", "ETCUSDT", "ALGOUSDT", "DYDXUSDT", "TRXUSDT"]


def ask(client, symbol: str):
    """問 allOrders。回 list,或 None 表示問不到。"""
    try:
        data = client.get(READ_ONLY["std_orders"], {"symbol": symbol})
    except PrivateCallFailed:
        return None
    return data if isinstance(data, list) else None


def ids(rows) -> frozenset:
    """訂單編號的集合。**比集合,不比筆數。**

    比筆數是上一版的 bug:12 ≠ 49 被讀成「分辨得出」,
    而那兩個數字的差別完全不是我以為的那個意思。
    """
    return frozenset(
        str(r.get("orderId")) for r in (rows or [])
        if isinstance(r, dict) and r.get("orderId") is not None)


def main(argv) -> int:
    load_env()
    try:
        client = ReadOnlyClient()
    except CredentialsMissing as e:
        print(f"✗ {e}")
        return 2

    # ── 一、把整本訂單史釣出來 ────────────────────────
    print(f"\n{LINE}\n  一、認不得的代號會回整本訂單史\n{LINE}\n")
    everything = ask(client, BAIT)
    if everything is None:
        print(f"  {BAIT} 問不到 —— 這一版的整套判別式建立在"
              "「認不得就回整本」上,那個前提不成立了。")
        print("  不要往下讀,先看交易所到底回了什麼。")
        return 1

    all_ids = ids(everything)
    traded = sorted({r.get("symbol") for r in everything
                     if isinstance(r, dict) and r.get("symbol")})
    print(f"  用 {BAIT}(我編的)問,交易所回了 {len(everything)} 筆。")
    print(f"  裡面有 {len(traded)} 個不重複的標的 —— "
          "**這是交易所自己列出來的,不是推論**:\n")
    strategy = {s.replace("-", "") for s in SYMBOLS}
    for sym in traded:
        n = sum(1 for r in everything if r.get("symbol") == sym)
        tag = "  ← 策略現役" if sym in strategy else ""
        print(f"    {sym:<14}{n:>4} 筆{tag}")

    missing = sorted(strategy - set(traded))
    if missing:
        print(f"\n  策略現役七幣裡,**訂單史上沒出現過**的:"
              f"{', '.join(missing)}")
        print("    「沒交易過」不等於「不能交易」—— 往下看逐個問的結果。")

    # ── 二、逐個候選,比訂單編號的集合 ────────────────
    candidates = list(argv) if argv else sorted(strategy
                                                | set(FROM_SCREENSHOT))
    print(f"\n{LINE}\n  二、逐個問 —— 比訂單編號的集合,不比筆數\n{LINE}\n")
    print(f"  {'代號':<12}{'筆數':>6}  {'判讀'}")

    known, unknown, unclear = [], [], []
    for sym in candidates:
        rows = ask(client, sym)
        if rows is None:
            unclear.append(sym)
            print(f"  {sym:<12}{'—':>6}  問不到(錯誤)")
            continue
        got = ids(rows)
        if got == all_ids and all_ids:
            unknown.append(sym)
            verdict = "**認不得** —— 回的就是整本,過濾沒生效"
        elif got <= all_ids:
            known.append(sym)
            verdict = ("認得,有成交史" if got else
                       "認得,但沒有成交史(過濾生效且結果為空)")
        else:
            unclear.append(sym)
            verdict = "?? 回了整本以外的訂單 —— 前提不成立,別解讀"
        tag = " ← 策略現役" if sym in strategy else ""
        print(f"  {sym:<12}{len(rows):>6}  {verdict}{tag}")

    # ── 三、結論,以及它到不了哪裡 ────────────────────
    print(f"\n{LINE}\n  三、結論\n{LINE}\n")
    if unknown:
        print(f"  這個產品**認不得**:{', '.join(unknown)}")
        print("    → 這些不能在 U 本位標準合約上交易。")
    else:
        print("  所有候選都被認得(過濾都生效了)。")
    if unclear:
        print(f"\n  判讀不了:{', '.join(unclear)} —— 不要當成任何一邊。")

    blocked = sorted(strategy & set(unknown))
    if blocked:
        print(f"\n  ❗ 策略現役七幣裡有 {len(blocked)} 個不能交易:"
              f"{', '.join(blocked)}")
        print("     交易池要改,而那是**策略層的決定**(§6)。")
        print("     交易池一改,回測的 Calmar 1.33 就不是這個池子的")
        print("     數字了,要重跑。")

    print(f"\n{LINE}")
    print("""  這一支到不了的地方

  「被認得」與「上架可交易」不是同一件事 —— allOrders 有可能拿
  全交易所的代號表做驗證,而不是這個產品的。「訂單史上出現過」
  才是硬事實,而它只涵蓋真的下過單的標的。

  這個產品**沒有 contracts 端點**,完整名單只能在 App 上翻。
  這一支不會假裝它有。""")
    print(LINE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
