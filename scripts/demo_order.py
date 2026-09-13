"""
第一筆 Demo 單 —— 驗證持倉欄位形狀 · 2026-09-13

═══ 這支為什麼存在 ═══
`account.py` 宣稱它的持倉欄位對齊 BingX Swap V2:
`positionAmt` / `avgPrice` / `liquidationPrice` / `markPrice`。

**那組欄位一個字都沒有被驗證過**,因為 Demo 帳戶有 0 個持倉。
而裡面有 liquidationPrice —— 強平價,算錯的後果不是數字難看,
是倉沒了(2026-09-10 UNI 那次就是這條線上的事)。

要驗證它,需要一個真的倉。這支開一個**最小的** Demo 倉,
然後立刻跑對帳去看那四個欄位。

═══ 預設什麼都不送 ═══
不帶 --send 就只是乾跑:把要送出去的東西完整印出來。
第一次跑任何新的下單路徑,都應該先看清楚要送什麼。

而且這支**只能對 Demo 下單** —— LIVE_ENABLED 是 False 的時候,
Trader 連建立都會失敗。

═══ 用法 ═══
    .venv/bin/python scripts/demo_order.py                # 乾跑
    .venv/bin/python scripts/demo_order.py --send         # 真的送(Demo)
    .venv/bin/python scripts/demo_order.py --close --send # 平掉它
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import interpreter

# 用錯直譯器的時候講人話,而不是丟一個 ModuleNotFoundError 讓人猜。
interpreter.require()

from core.config import load_env
from exchange.bingx import private, trade
from portfolio import specs

# 用 BTC:精度最好處理,而且它是這個交易池裡流動性最深的。
SYMBOL = "BTC-USDT"

# 名目金額。Demo 有 100,000 VST,這裡只用 20 —— 這一筆的目的是
# **看欄位長什麼樣**,不是測策略。金額越小,任何搞錯的代價越小。
NOTIONAL_VST = 20.0


def main() -> int:
    ap = argparse.ArgumentParser(description="開一筆最小的 Demo 倉")
    ap.add_argument("--send", action="store_true",
                    help="真的送出(不帶就只是乾跑)")
    ap.add_argument("--close", action="store_true",
                    help="平掉這個倉,而不是開倉")
    args = ap.parse_args()

    load_env()

    if private.is_live():
        print("\n  ✗ BINGX_ENV=live。這支只給 Demo 用。")
        print("    把 .env 改成 BINGX_ENV=demo 再跑。\n")
        return 2

    try:
        trader = trade.Trader()
    except private.CredentialsMissing as e:
        print(f"\n{e}\n")
        return 2
    except trade.NotAllowed as e:
        print(f"\n{e}\n")
        return 2

    reader = private.ReadOnlyClient()

    # 現在的倉
    positions = reader.positions(SYMBOL) or []
    rows = [p for p in positions
            if isinstance(p, dict) and abs(float(p.get("positionAmt") or 0))
            > 1e-12]

    if args.close:
        if not rows:
            print(f"\n  {SYMBOL} 沒有倉可以平。\n")
            return 0
        amount = abs(float(rows[0]["positionAmt"]))
        plan = trade.OrderPlan(
            symbol=SYMBOL, side=trade.SELL, position_side=trade.LONG,
            order_type=trade.MARKET, quantity=amount, reduce_only=True,
            client_id=trade.client_order_id("demo", SYMBOL, "close", "exit"),
            reason="平掉驗證用的 Demo 倉")
    else:
        if rows:
            print(f"\n  {SYMBOL} 已經有倉了({rows[0].get('positionAmt')})。")
            print("  先跑 scripts/reconcile.py 看欄位,或用 --close 平掉。\n")
            return 0

        # 數量要照交易所的精度,不能自己算一個浮點數(第十二條)
        try:
            price = float(reader.get(
                "/openApi/swap/v2/quote/price", {"symbol": SYMBOL})["price"])
        except Exception:
            marks = None
            price = None
        if not price:
            print("\n  拿不到價格,無法算數量。\n")
            return 1

        raw = NOTIONAL_VST / price
        try:
            quantity = specs.round_qty(SYMBOL, raw)
        except Exception as e:
            print(f"\n  規格快取讀不到,無法對齊數量精度:{e}")
            print("  先跑 .venv/bin/python scripts/daily.py\n")
            return 1

        if quantity <= 0:
            print(f"\n  {NOTIONAL_VST} VST 在 {price:,.2f} 之下不足最小數量。")
            print("  把 NOTIONAL_VST 調大一點再試。\n")
            return 1

        plan = trade.plan_entry(SYMBOL, quantity, "demo", "shape-check",
                                reason=f"驗證持倉欄位形狀(約 "
                                       f"{quantity * price:.2f} VST)")

    result = trader.submit(plan, confirm=args.send)

    print()
    print("═" * 58)
    print(f"  {plan.describe()}")
    print("═" * 58)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print()

    if result.get("dry_run"):
        print("  這是乾跑,什麼都沒有送出去。")
        print("  看清楚上面的參數之後,加 --send 再跑一次。")
        print()
        print("  ⚠️ 參數的鍵名還沒有經過實測。錯了 Demo 會直接拒單,")
        print("     而那個錯誤訊息會告訴我們哪裡要改 —— 成本是零。")
    else:
        print("  送出去了。現在跑對帳看持倉欄位:")
        print("      .venv/bin/python scripts/reconcile.py")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
