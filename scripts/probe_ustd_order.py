"""
U 本位標準合約到底能不能下單 —— 這次用 POST 問 · 2026-09-13

═══ 我上一輪的探測有個漏洞 ═══
全部用 **GET**。而 `cswap/v1/trade/order` 用 GET 回的是:

    'QueryOrderRequest.Symbol' failed on the 'required' tag

**QueryOrderRequest** —— GET 是「查訂單」,**POST 才是「下訂單」**。

所以 `contract/v1/order` 回 100400,可能只是「這個路徑沒有 GET
處理器」,不是「這個端點不存在」。用錯動詞去問,得到的答案沒有意義。

這跟 104414 那件事是同一類:**我讀懂了字面,沒讀懂語意。**

═══ 為什麼 POST 也是安全的 ═══
一、**不帶任何交易參數。** 沒有 symbol / side / quantity 的下單請求
    不可能被接受 —— 交易所會在參數驗證就擋掉。
二、**這把金鑰沒有交易權限。** 已經被 code=100004 證明過一次。

兩道獨立的保險。而回應會告訴我們:

    404 / code 100400        -> 真的不存在
    參數錯誤(點名缺哪個欄位) -> **存在**
    code 100004(缺權限)      -> **存在**

═══ 這件事為什麼重要 ═══
如果 U 本位標準合約真的沒有下單 API,那「只做標準合約 + 自動下單」
在 BingX 上就是互斥的,而執政官必須在三個選項裡挑一個。

那是一個不該建立在「我用錯 HTTP 動詞」上面的結論。

用法:  .venv/bin/python scripts/probe_ustd_order.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import interpreter

interpreter.require()

from core import ratelimit
from core.config import load_env
from exchange.bingx import private

# U 本位標準合約(contract/v1)的下單候選路徑。
CANDIDATES = [
    "/openApi/contract/v1/order",
    "/openApi/contract/v1/trade/order",
    "/openApi/contract/v1/allOrders",      # 對照組:已知存在的 GET 端點
]

# 幣本位那邊已知可用,拿來當「POST 探測本身有效」的對照。
CONTROL = "/openApi/cswap/v1/trade/order"


def probe(client, method: str, path: str) -> str:
    """
    **不帶任何交易參數。** 只有 timestamp 與 recvWindow。
    """
    query = {"timestamp": int(time.time() * 1000),
             "recvWindow": private.RECV_WINDOW_MS}
    sig = private.signature(query, client.creds.secret)
    url = f"{client.base}{path}?{urlencode(query)}&signature={sig}"

    try:
        resp = ratelimit.requests_request(
            None, method, url,
            headers={"X-BX-APIKEY": client.creds.key}, timeout=20.0)
    except ratelimit.RateLimited:
        return "被限流,等一下再問"
    except Exception as e:
        return f"連不上:{type(e).__name__}"

    try:
        body = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code},回的不是 JSON"

    code = str(body.get("code", "0"))
    msg = str(body.get("msg") or "")

    if code == "0":
        return f"✓ 回 code 0(HTTP {resp.status_code})"
    if code == "100400":
        return f"✗ 不存在(100400)"
    if code == "100004":
        return "✓ **存在** —— 只是金鑰缺交易權限(100004)"
    if code == "104414" or "Invalid parameter" in msg or "required" in msg:
        return f"✓ **存在** —— 參數驗證擋下:{msg[:90]}"
    return f"? code {code}:{msg[:90]}"


def main() -> int:
    load_env()
    try:
        client = private.ReadOnlyClient()
    except private.CredentialsMissing as e:
        print(f"\n{e}\n")
        return 2

    print()
    print("═" * 64)
    print("  U 本位標準合約:GET 與 POST 各問一次")
    print("  不帶任何交易參數 —— 沒有 symbol / side / quantity 的單")
    print("  不可能成交,而且這把金鑰也沒有交易權限")
    print("═" * 64)
    print()

    for path in CANDIDATES:
        print(f"  {path}")
        for method in ("GET", "POST"):
            print(f"      {method:<5} {probe(client, method, path)}")
        print()

    print("═" * 64)
    print("  對照組:幣本位那個已知存在的下單端點")
    print("═" * 64)
    print(f"  {CONTROL}")
    for method in ("GET", "POST"):
        print(f"      {method:<5} {probe(client, method, CONTROL)}")

    print()
    print("═" * 64)
    print()
    print("  怎麼讀:")
    print("    對照組的 POST 要能問出東西 —— 否則是我的探測壞了,")
    print("    不是端點不存在。**先看對照組再看上面。**")
    print()
    print("  如果 U 本位標準合約的 POST 全部是 100400,")
    print("  那才真的能下結論:它沒有下單 API。")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
