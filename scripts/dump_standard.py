"""
標準合約回的東西到底長什麼樣 —— 原始形狀 · 2026-09-13

═══ 為什麼需要這一支 ═══
探測顯示 `allPosition` 回了「data 1 項」,而 reconcile.py 與
where_is_it.py 都說「標準持倉 0 筆」。

兩邊不可能都對。原因是我的 count() 只會數 list —— 交易所如果回的是
dict,它就數成 0。**這是同一個病的第五次**,只是這次是解析不是範圍。

在改任何程式之前,先看交易所**實際回了什麼**。
猜形狀是這整段路上每一次出錯的共同原因。

═══ 順便回答一個更大的問題 ═══
`cswap/v1/market/contracts` 回了 20 個合約 —— 那正是 2026-09-10 判定
「不存在」而讓標準合約整個被擱置的東西。它現在活了。

如果那 20 個合約帶著**精度、最小量、費率**,那麼第十二條的前提就
成立了,標準合約自動下單才談得上可能。沒有那些欄位,即使下單端點
存在也下不出正確的單。

所以這支把合約清單的**第一筆完整印出來**,一個欄位都不省略。

═══ 只讀 ═══
全部是 GET,沒有任何交易參數。

用法:  .venv/bin/python scripts/dump_standard.py
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

TARGETS = [
    ("合約清單", "/openApi/cswap/v1/market/contracts"),
    ("持倉", "/openApi/contract/v1/allPosition"),
    ("餘額", "/openApi/contract/v1/balance"),
]


def raw(client, path: str):
    query = {"timestamp": int(time.time() * 1000),
             "recvWindow": private.RECV_WINDOW_MS}
    sig = private.signature(query, client.creds.secret)
    url = f"{client.base}{path}?{urlencode(query)}&signature={sig}"
    resp = ratelimit.requests_request(
        None, "GET", url,
        headers={"X-BX-APIKEY": client.creds.key}, timeout=15.0)
    return resp.json()


def shape(value) -> str:
    if isinstance(value, list):
        return f"list,{len(value)} 筆"
    if isinstance(value, dict):
        return f"dict,{len(value)} 個鍵"
    return type(value).__name__


def main() -> int:
    load_env()

    try:
        client = private.ReadOnlyClient()
    except private.CredentialsMissing as e:
        print(f"\n{e}\n")
        return 2

    print()
    for label, path in TARGETS:
        print("═" * 62)
        print(f"  {label}   {path}")
        print("═" * 62)
        try:
            body = raw(client, path)
        except Exception as e:
            print(f"  查不到:{type(e).__name__}: {e}\n")
            continue

        data = body.get("data")
        print(f"  外層 code={body.get('code')}  data 形狀:{shape(data)}")
        print()

        # 取出「一筆」來看,不管它是 list 還是 dict 包 list
        sample = data
        if isinstance(data, dict):
            print(f"  dict 的鍵:{sorted(data)}")
            print()
            for key, value in data.items():
                if isinstance(value, list) and value:
                    print(f"  data[{key!r}] 是 list,{len(value)} 筆 "
                          f"—— 真正的資料在這裡")
                    sample = value
                    break
        if isinstance(sample, list):
            sample = sample[0] if sample else None

        if sample is None:
            print("  (空的)")
            print()
            continue

        print("  第一筆的完整內容:")
        print(json.dumps(sample, ensure_ascii=False, indent=4))
        print()

    print("═" * 62)
    print()
    print("  要看的三件事:")
    print("    一、持倉那一筆有沒有 liquidationPrice(或類似的強平價欄位)")
    print("    二、合約清單有沒有帶精度 / 最小量 / 費率(第十二條的前提)")
    print("    三、欄位名跟 account.py 宣稱的對不對得上")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
