"""
U 本位標準合約到底能交易哪些幣 —— 沒有端點可問,只能試 · 2026-09-13

═══ 這是現在最大的未知 ═══
`/openApi/contract/v1` **沒有 contracts 端點**。所以「有哪些標的可以
交易」這件事,程式問不到。

而策略現役的七個幣是 BTC / ETH / SOL / BNB / XRP / AAVE / UNI。
執政官 2026-09-13 的 App 截圖上,U 本位標準合約那一頁看得到的是:
BTCUSDT、ETHUSDT、XRPUSDT、LINKUSDT、LTCUSDT、BCHUSDT、ETCUSDT、
ALGOUSDT、DYDXUSDT、TRXUSDT —— **SOL 與 BNB 不在裡面**。

但那是一張**捲動中的清單的一部分**,不是完整名單。
拿它當結論就是這個專案已經犯過六次的那個錯:
**一個聽起來很確定、但其實沒有被完整問過的答案。**

═══ 怎麼在沒有端點的情況下問 ═══
`allOrders` 要帶 symbol。拿不同的 symbol 去問,回應會不一樣 ——
問題是「不一樣」長什麼樣,我們不知道。所以這一支先建立**對照組**:

    已知存在  FLOCKUSDT   有持倉,一定是真的
    已知不存在 ZZZZZUSDT   隨便編的,一定是假的

兩個的回應如果**長得不一樣**,那個差別就是判別式,後面的候選才有
意義。如果**長得一樣**,那 allOrders 就分辨不出存不存在 ——
這一支會直說,而不是硬給一個答案。

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

# 對照組。**先看這兩個,再看下面。**
KNOWN_REAL = "FLOCKUSDT"      # 2026-09-13 實測有持倉
KNOWN_FAKE = "ZZZZZUSDT"      # 編的

# App 截圖上看得到的(可能不完整)
FROM_SCREENSHOT = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "LINKUSDT", "LTCUSDT",
                   "BCHUSDT", "ETCUSDT", "ALGOUSDT", "DYDXUSDT", "TRXUSDT"]


def ask(client, symbol: str) -> tuple:
    """問 allOrders。回 (分類, 原始說明)。**不解讀,只分類。**"""
    try:
        data = client.get(READ_ONLY["std_orders"], {"symbol": symbol})
    except PrivateCallFailed as e:
        return "錯誤", str(e)
    if data is None:
        return "回 null", "code 0 但 data 是 null"
    if isinstance(data, list):
        return (f"回 {len(data)} 筆", "有成交史" if data else "空清單")
    return "回非清單", repr(data)[:80]


def main(argv) -> int:
    load_env()
    try:
        client = ReadOnlyClient()
    except CredentialsMissing as e:
        print(f"✗ {e}")
        return 2

    print(f"\n{LINE}\n  對照組 —— **先看這兩行**\n{LINE}\n")
    real = ask(client, KNOWN_REAL)
    fake = ask(client, KNOWN_FAKE)
    print(f"  已知存在  {KNOWN_REAL:<12} {real[0]:<12} {real[1]}")
    print(f"  已知不存在 {KNOWN_FAKE:<12} {fake[0]:<12} {fake[1]}")

    discriminates = real[0] != fake[0]
    print()
    if discriminates:
        print("  ✓ 兩者回應不同 —— allOrders 分辨得出存不存在,")
        print(f"    「不存在」長這樣:{fake[0]}")
    else:
        print("  ✗ **兩者回應一樣。** allOrders 分辨不出存不存在 ——")
        print("    下面那張表只能告訴你「哪些有成交史」,")
        print("    **不能**告訴你「哪些可以交易」。不要把它當成名單。")

    candidates = list(argv) if argv else sorted(
        {s.replace("-", "") for s in SYMBOLS} | set(FROM_SCREENSHOT))

    print(f"\n{LINE}\n  候選 {len(candidates)} 個\n{LINE}\n")
    print(f"  {'代號':<12}{'結果':<14}{'說明'}")
    strategy = {s.replace("-", "") for s in SYMBOLS}
    for sym in candidates:
        kind, detail = ask(client, sym)
        tag = " ← 策略現役" if sym in strategy else ""
        verdict = "?"
        if discriminates:
            verdict = "存在" if kind != fake[0] else "**不存在**"
        print(f"  {sym:<12}{kind:<14}{detail}{tag}")
        if discriminates:
            print(f"  {'':<12}{verdict}")

    print(f"\n{LINE}\n  怎麼讀\n{LINE}\n")
    print("  · 對照組不分辨的話,這張表只是「哪些有成交史」——")
    print("    一個從沒交易過的標的會長得跟不存在的一模一樣。")
    print("  · 真正確定的方法只有一個:**在 App 上翻完那份清單**。")
    print("    這個產品沒有 contracts 端點,程式問不到完整名單。")
    print()
    print("  · 如果 SOL / BNB / AAVE / UNI 真的不在 U 本位標準合約上,")
    print("    那不是介面層的問題,是**交易池要改**——")
    print("    而交易池是策略層的決定(第六條),不是我能自己換掉的。")
    print("    交易池換掉,回測的 Calmar 1.33 就不是這個池子的數字了,")
    print("    要重跑。")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
