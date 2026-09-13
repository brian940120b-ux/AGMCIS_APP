"""
標準合約可行性:剩下的四個未知 · 2026-09-13

═══ 已知 ═══
cswap 的 API 開了。合約清單、持倉、餘額、歷史訂單都讀得到,
下單端點也存在(HTTP 400 = 缺參數,不是不存在)。

標準合約的錢包就是 App 裡那一個(VST 119,113),
而永續 API 開的是另一個全新的 100,000 —— 謎團解開了。

═══ 還不知道的四件事,每一件都可能是攔路虎 ═══

一、**那 20 個合約是哪些?** 現在的七幣交易池在不在裡面?
    合約清單寫 `NEAR-USD`,持倉卻寫 `FLOCKUSDT` —— 兩種格式並存,
    要弄清楚哪一種才是下單要送的。

二、**有沒有 K 線?** 50 日均線策略沒有歷史日線就算不出訊號。
    (退路:用永續的 K 線算訊號、在標準合約執行 —— 同一個標的,
    價格序列幾乎一樣。但那要另外論證,不能默默就這樣做。)

三、**費率在哪裡?** 永續的合約清單帶 takerFeeRate,標準的沒有。
    回測與記帳都需要它。沒有的話,成本模型是猜的。

四、**下單要送什麼?** HTTP 400 證明端點存在,但沒說要什麼參數。
    把回應內容完整印出來 —— 交易所通常會在錯誤訊息裡點名缺了誰。

═══ 只讀,不下單 ═══
下單端點那一發**不帶任何交易參數**,只為了看它的錯誤訊息說什麼。
沒有 symbol / side / quantity 的請求不可能成交,而這把金鑰也沒有
交易權限。

用法:  .venv/bin/python scripts/probe_standard2.py
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
from portfolio.paper import SYMBOLS

KLINE_CANDIDATES = [
    "/openApi/cswap/v1/market/klines",
    "/openApi/cswap/v1/quote/klines",
    "/openApi/cswap/v1/market/kline",
]
FEE_CANDIDATES = [
    "/openApi/cswap/v1/user/commissionRate",
    "/openApi/contract/v1/commissionRate",
    "/openApi/cswap/v1/market/premiumIndex",
]


def call(client, path: str, extra: dict | None = None):
    query = dict(extra or {})
    query["timestamp"] = int(time.time() * 1000)
    query["recvWindow"] = private.RECV_WINDOW_MS
    sig = private.signature(query, client.creds.secret)
    url = f"{client.base}{path}?{urlencode(query)}&signature={sig}"
    try:
        resp = ratelimit.requests_request(
            None, "GET", url,
            headers={"X-BX-APIKEY": client.creds.key}, timeout=15.0)
    except ratelimit.RateLimited:
        return None, "被限流"
    except Exception as e:
        return None, f"{type(e).__name__}"
    try:
        return resp, resp.json()
    except ValueError:
        return resp, resp.text[:200]


def verdict(resp, body) -> str:
    if resp is None:
        return str(body)
    if isinstance(body, dict):
        code = str(body.get("code", "0"))
        if code == "0":
            data = body.get("data")
            n = len(data) if isinstance(data, (list, dict)) else "?"
            return f"✓ 可用({n} 項)"
        if code in ("100400", "104414"):
            return f"✗ 不存在({code})"
        return f"✓ 存在,但 code {code}:{str(body.get('msg'))[:60]}"
    return f"HTTP {resp.status_code}:{str(body)[:60]}"


def main() -> int:
    load_env()
    try:
        client = private.ReadOnlyClient()
    except private.CredentialsMissing as e:
        print(f"\n{e}\n")
        return 2

    print()
    print("═" * 62)
    print("  一、那 20 個合約是哪些")
    print("═" * 62)
    _r, body = call(client, "/openApi/cswap/v1/market/contracts")
    rows = body.get("data") if isinstance(body, dict) else None
    if isinstance(rows, list):
        names = [str(r.get("symbol")) for r in rows if isinstance(r, dict)]
        for i in range(0, len(names), 4):
            print("    " + "  ".join(f"{n:<14}" for n in names[i:i + 4]))
        print()
        base = {n.split("-")[0].replace("USDT", "") for n in names}
        want = {s.split("-")[0] for s in SYMBOLS}
        print(f"    現在的交易池:{'、'.join(sorted(want))}")
        hit = sorted(want & base)
        miss = sorted(want - base)
        print(f"    ✓ 標準合約有:{'、'.join(hit) or '(一個都沒有)'}")
        print(f"    ✗ 標準合約沒有:{'、'.join(miss) or '(全部都有)'}")
    else:
        print(f"    讀不到:{verdict(_r, body)}")

    print()
    print("═" * 62)
    print("  二、有沒有 K 線(沒有就算不出 50 日均線)")
    print("═" * 62)
    sample = names[0] if isinstance(rows, list) and rows else "NEAR-USD"
    for path in KLINE_CANDIDATES:
        r, b = call(client, path, {"symbol": sample, "interval": "1d",
                                   "limit": 5})
        print(f"    {verdict(r, b)}")
        print(f"      {path}")

    print()
    print("═" * 62)
    print("  三、費率在哪裡(回測與記帳都需要)")
    print("═" * 62)
    for path in FEE_CANDIDATES:
        r, b = call(client, path, {"symbol": sample})
        print(f"    {verdict(r, b)}")
        print(f"      {path}")

    print()
    print("═" * 62)
    print("  四、下單端點要什麼參數(不帶交易參數,只看它抱怨什麼)")
    print("═" * 62)
    r, b = call(client, "/openApi/cswap/v1/trade/order")
    print(f"    HTTP {r.status_code if r else '?'}")
    print("    回應內容:")
    print("    " + json.dumps(b, ensure_ascii=False)[:400]
          if isinstance(b, (dict, list)) else f"    {b}")

    print()
    print("═" * 62)
    print()
    print("  沒有 K 線的話還有一條路:用**永續**的日線算訊號,")
    print("  在標準合約執行 —— 同一個標的,價格序列幾乎一樣。")
    print("  但那要另外論證清楚,不能默默就這樣做。")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
