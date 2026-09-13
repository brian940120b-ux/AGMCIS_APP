"""
標準合約:動手之前最後三個必須確定的事 · 2026-09-13

═══ 已經確定的 ═══
七個幣全部都有(BTC/ETH/SOL/BNB/XRP/AAVE/UNI-USD)、K 線可讀、
費率端點可讀、下單端點存在。

═══ 還沒確定,而且每一個都會改寫帳本 ═══

一、**是幣本位還是 U 本位?**
    合約清單寫 `BTC-USD` —— 在多數交易所,`-USD` 代表**幣本位**
    (反向合約):保證金與損益都用幣計價,不是 USDT。

    `account.py` 整本帳是照 **U 本位線性合約**寫的:
    損益 = 數量 × (賣價 − 買價),用 USDT 結算。
    幣本位的公式完全不同:損益 = 面額 × (1/買價 − 1/賣價)。

    **搞錯這件事,帳本每一個數字都是錯的。**
    可是持倉那筆又寫 `FLOCKUSDT`、`initialMargin: 15.84` ——
    看起來像 USDT。兩邊對不上,所以要問清楚。

二、**K 線有多長?**
    50 日均線至少要 50 根。回測要 3.3 年約 1200 根。
    拿不到足夠歷史,策略就無法在這個市場重新驗證。

三、**費率是多少、有沒有資金費?**
    `exchange/base.py` 寫「Standard Futures 沒有資金費率」,
    但 `cswap/v1/market/premiumIndex` 是通的 —— 那通常就是資金費率。
    那句話可能跟 standard.py 的「無法實作」一樣過期了。

用法:  .venv/bin/python scripts/probe_standard3.py
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


def call(client, path: str, extra: dict | None = None):
    query = dict(extra or {})
    query["timestamp"] = int(time.time() * 1000)
    query["recvWindow"] = private.RECV_WINDOW_MS
    sig = private.signature(query, client.creds.secret)
    url = f"{client.base}{path}?{urlencode(query)}&signature={sig}"
    try:
        resp = ratelimit.requests_request(
            None, "GET", url,
            headers={"X-BX-APIKEY": client.creds.key}, timeout=20.0)
        return resp.json()
    except Exception as e:
        return {"code": "-1", "msg": f"{type(e).__name__}: {e}"}


def show(label, body, limit=600):
    print(f"  {label}")
    print("  " + json.dumps(body, ensure_ascii=False)[:limit])
    print()


def main() -> int:
    load_env()
    try:
        client = private.ReadOnlyClient()
    except private.CredentialsMissing as e:
        print(f"\n{e}\n")
        return 2

    print()
    print("═" * 62)
    print("  一、幣本位還是 U 本位 —— 這決定帳本的每一個公式")
    print("═" * 62)
    show("ticker(看報價與計價單位)",
         call(client, "/openApi/cswap/v1/market/ticker",
              {"symbol": "BTC-USD"}))
    show("深度(看數量的單位是幣還是張)",
         call(client, "/openApi/cswap/v1/market/depth",
              {"symbol": "BTC-USD", "limit": 2}), 400)
    show("持倉(現有那一筆的完整欄位)",
         call(client, "/openApi/contract/v1/allPosition"), 800)

    print("═" * 62)
    print("  二、K 線有多長(50 日均線要 50 根,回測要約 1200 根)")
    print("═" * 62)
    for limit in (1000, 1440):
        body = call(client, "/openApi/cswap/v1/market/klines",
                    {"symbol": "BTC-USD", "interval": "1d", "limit": limit})
        rows = body.get("data") if isinstance(body, dict) else None
        if isinstance(rows, list) and rows:
            first, last = rows[0], rows[-1]
            def when(row):
                t = row.get("time") or row.get("t") or (
                    row[0] if isinstance(row, list) else None)
                try:
                    from datetime import datetime, timezone
                    return f"{datetime.fromtimestamp(int(t)/1000, timezone.utc):%Y-%m-%d}"
                except Exception:
                    return str(t)[:20]
            print(f"    limit={limit:<5} 拿到 {len(rows):>5} 根  "
                  f"{when(first)} ~ {when(last)}")
        else:
            print(f"    limit={limit:<5} {str(body)[:120]}")
    print()
    body = call(client, "/openApi/cswap/v1/market/klines",
                {"symbol": "BTC-USD", "interval": "1d", "limit": 3})
    show("K 線一根長什麼樣", body, 400)

    print("═" * 62)
    print("  三、費率與資金費")
    print("═" * 62)
    show("手續費率", call(client, "/openApi/cswap/v1/user/commissionRate"))
    show("premiumIndex(base.py 說標準合約沒有資金費 —— 真的嗎)",
         call(client, "/openApi/cswap/v1/market/premiumIndex",
              {"symbol": "BTC-USD"}))

    print("═" * 62)
    print()
    print("  ⚠️ 最關鍵的是第一項。幣本位與 U 本位的損益公式完全不同,")
    print("     搞錯的話帳本每一個數字都是錯的,而且紙上看不出來。")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
