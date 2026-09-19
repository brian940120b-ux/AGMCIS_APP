"""
探針:系統看不看得見「這個倉有沒有設止損」· 2026-09-19

═══ 為什麼要問這個 ═══
指令單上最重的一句話是:

    ⑤ 止損 …… 一定要設 —— 這是機器死掉時唯一的保護

而系統**從來沒有驗證過那件事有沒有發生**。

`exchange/bingx/trade.unprotected()` 的 docstring 自己寫著
「這是實盤最重要的一條巡檢」,然後整個倉庫沒有人呼叫它 ——
它是給自動實盤用的,而實盤是關的。但**手動路徑現在就在跑**:
人按了單、開了真倉,而 `StandardPosition` 這個型別裡
**根本沒有止損欄位**。

我們唯一看過的一筆真倉(2026-09-18 執政官的 AAVE)止損就是 `--`。
系統沒有吭一聲,因為它看不到。

═══ 這支要回答的 ═══
一、`allPosition` 的原始回應裡,**到底有沒有**止盈止損的欄位?
    (parse_position 只讀它認得的那幾個 key,其餘靜靜丟掉)
二、止損如果是掛成一張條件單,`allOrders` 看不看得到?

═══ 這支不下判斷 ═══
把原始 JSON 的 key 與值原封不動印出來。
2026-09-18 我已經把 104414 讀成「端點不存在」(其實是參數錯誤)、
把網址裡的 /perpetual/ 讀成「這是永續的連結」(其實是標準合約的)。
兩次都是拿一個看起來像答案的東西當答案。

**看得到欄位** -> 接上去,面板就能說「這個倉沒有止損」
**看不到欄位** -> 那是一個關不掉的洞,要如實寫在面板上,
                  而不是繼續叫人設止損卻永遠不檢查
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import interpreter

interpreter.require()

from core.config import load_env

load_env()

LINE = "═" * 64

#: 各家交易所用過的止盈止損欄位名。**只用來提示,不用來判斷** ——
#: 沒中不代表沒有,可能只是叫別的名字,所以下面一律把完整 key 列印出來。
HINTS = ("stop", "sl", "tp", "takeprofit", "profit", "trigger", "cond")


def looks_like_stop(key: str) -> bool:
    k = key.lower()
    return any(h in k for h in HINTS)


def main() -> int:
    from exchange.bingx.standard import BingXStandardUSDT

    print(f"\n{LINE}\n  止損看得見嗎 —— U 本位標準合約\n{LINE}")
    print("  這支不下判斷,只把交易所回的 key 與值原樣印出來。\n")

    api = BingXStandardUSDT()

    # ── 一、持倉的原始欄位 ────────────────────────────
    print(f"{LINE}\n  一、allPosition 的原始欄位\n{LINE}")
    try:
        rows = api.positions()
    except Exception as e:                           # noqa: BLE001
        print(f"  問不到:{type(e).__name__}: {e}")
        rows = []

    if not rows:
        print("  **目前沒有持倉**,所以看不到欄位長什麼樣。")
        print("  請在 App 開一個小倉(可以是 VST 模擬倉),"
              "設好止損,再跑一次這支。")
    for i, row in enumerate(rows, 1):
        print(f"\n  ── 第 {i} 筆 ──")
        if not isinstance(row, dict):
            print(f"    不是 dict,是 {type(row).__name__}:{row!r}")
            continue
        for k in sorted(row):
            flag = "  ← 像止損/止盈" if looks_like_stop(k) else ""
            print(f"    {k:<24}{row[k]!r}{flag}")
        hits = [k for k in row if looks_like_stop(k)]
        print(f"    → 這一筆裡看起來像止損的 key:{hits or '一個都沒有'}")

    # ── 二、掛單裡看不看得到 ──────────────────────────
    print(f"\n{LINE}\n  二、allOrders 裡有沒有條件單\n{LINE}")
    syms = sorted({r.get("symbol") for r in rows
                   if isinstance(r, dict) and r.get("symbol")})
    if not syms:
        syms = ["BTCUSDT"]
        print("  沒有持倉,拿 BTCUSDT 當樣本看欄位形狀。")
    for sym in syms[:3]:
        print(f"\n  ── {sym} ──")
        try:
            orders = api.orders(sym)
        except Exception as e:                       # noqa: BLE001
            print(f"    問不到:{type(e).__name__}: {e}")
            continue
        if not orders:
            print("    沒有紀錄。")
            continue
        print(f"    {len(orders)} 筆。第一筆的完整欄位:")
        first = orders[0]
        if isinstance(first, dict):
            for k in sorted(first):
                flag = "  ← 像止損/止盈" if looks_like_stop(k) else ""
                print(f"      {k:<24}{first[k]!r}{flag}")
        types = sorted({str(o.get("type") or o.get("orderType") or "?")
                        for o in orders if isinstance(o, dict)})
        print(f"    出現過的訂單型別:{types}")

    print(f"\n{LINE}\n  怎麼看這份輸出\n{LINE}")
    print("""
  要找的是:**某一個 key 的值,等於你在 App 上設的那個止損價。**

  · 找得到               -> 接上去,面板就能說「這個倉沒有止損」,
                            而那是目前最大的一個洞
  · 持倉裡沒有、掛單裡有  -> 止損是一張獨立的條件單,
                            要從 allOrders 判斷(注意 allOrders 是
                            **成交史**,未成交的條件單可能不在裡面)
  · 兩邊都沒有           -> 這個產品的唯讀 API 看不到止損。
                            那就**如實寫在面板上**:系統叫你設止損,
                            但它永遠不知道你設了沒有 ——
                            而不是繼續假裝那條規則有人在管

  ⚠️ 沒有持倉的話這支問不出東西。請先在 App 開一個小倉(VST 模擬倉
     就可以),**設好止損**,再跑一次。
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
