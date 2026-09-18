"""
探針:有沒有「一次拿全部幣種現價」的公開端點 · 2026-09-18

執政官問:「能做到即時更新嗎?」

現在面板每 15 秒更新一次,而不能更快的原因是**速率預算**,不是技術:
每一輪要替 7 個幣各打一次 K 線,5 秒一輪 = 84 次/分,而全系統的
預算是 2 次/秒 = 120 次/分。光面板就吃掉七成,日常作業、巡檢、
交易所帳戶查詢全部要跟它搶。

一次拿回全部幣種的話,一輪只要 1 次 —— 5 秒一輪也只有 12 次/分。

═══ 這支不是用來「確認我猜對了」═══
我沒有辦法從開發機連到交易所(proxy 擋著),所以我**不知道**這些
端點存不存在、回什麼形狀。這支把候選端點一個個打過去,原封不動
把回應印出來。

**它不判斷成功失敗,它只呈現證據。** 這一點是刻意的:
2026-09-18 我把 `104414` 讀成「端點不存在」(實際是參數錯誤),
又把一個網址裡的 `/perpetual/` 讀成「這是永續的連結」(實際是
標準合約的)。兩次都是拿一個看起來像答案的東西當答案。

所以這支只做一件事:**把交易所真的說了什麼貼出來。**
判斷留給看得到全貌的人。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import ratelimit

BASE = "https://open-api.bingx.com"

#: 候選。全部是公開行情端點,不需要金鑰。
CANDIDATES = [
    ("全部現價(不帶 symbol)", "/openApi/swap/v2/quote/price", ""),
    ("單一現價(對照組)", "/openApi/swap/v2/quote/price", "symbol=BTC-USDT"),
    ("全部 ticker(不帶 symbol)", "/openApi/swap/v2/quote/ticker", ""),
    ("單一 ticker(對照組)", "/openApi/swap/v2/quote/ticker",
     "symbol=BTC-USDT"),
    ("premiumIndex(不帶 symbol)", "/openApi/swap/v2/quote/premiumIndex", ""),
]

LINE = "═" * 64


def probe(label: str, path: str, query: str) -> None:
    url = BASE + path + (f"?{query}" if query else "")
    print(f"\n{LINE}\n  {label}\n  {url}\n{LINE}")
    try:
        with ratelimit.urlopen(url, timeout=12) as r:
            raw = r.read().decode("utf-8", "replace")
            status = r.status
    except Exception as e:                           # noqa: BLE001
        print(f"  連不上:{type(e).__name__}: {e}")
        return

    print(f"  HTTP {status}")
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        print(f"  不是 JSON,前 400 字:\n  {raw[:400]}")
        return

    print(f"  code = {d.get('code')!r}   msg = {d.get('msg')!r}")
    data = d.get("data")
    if isinstance(data, list):
        print(f"  data 是**陣列**,{len(data)} 筆")
        for row in data[:3]:
            print(f"    {json.dumps(row, ensure_ascii=False)}")
        if len(data) > 3:
            print(f"    …另外 {len(data) - 3} 筆")
        syms = {r.get("symbol") for r in data if isinstance(r, dict)}
        want = {"BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT",
                "XRP-USDT", "AAVE-USDT", "UNI-USDT"}
        hit = sorted(want & syms)
        print(f"  我們那七個幣裡,這裡有 {len(hit)} 個:{hit}")
    elif isinstance(data, dict):
        print("  data 是**物件**(所以不是「一次拿全部」):")
        print(f"    {json.dumps(data, ensure_ascii=False)[:400]}")
    else:
        print(f"  data = {data!r}")
        print(f"  整包前 400 字:\n  {raw[:400]}")


def main() -> int:
    print(f"\n{LINE}\n  現價來源探針 —— 有沒有「一次拿全部」的端點"
          f"\n{LINE}")
    print("  全部是公開端點,不需要金鑰。")
    print("  ⚠️ 這支**不下判斷**,它只把交易所真的回了什麼貼出來。")
    for label, path, query in CANDIDATES:
        probe(label, path, query)

    print(f"\n{LINE}\n  怎麼看這份輸出\n{LINE}")
    print("""
  要找的是:**code = '0' 而且 data 是一個涵蓋我們七個幣的陣列。**

  · data 是陣列而且七個幣都在   -> 可以一次拿全部,面板能做到 5 秒
  · data 是物件(單一個幣)     -> 不帶 symbol 也只回一個,沒有用
  · code 不是 '0'              -> 把 code 與 msg 一起貼回來,
                                  **不要自己解讀成「端點不存在」**
                                  (100400 才是沒有這個 handler;
                                   104414 是參數錯誤,端點是在的)

  對照組的用途:如果單一查詢成功而不帶 symbol 失敗,
  那證明端點活著、只是不支援「全部」—— 那是兩件不同的事。
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
