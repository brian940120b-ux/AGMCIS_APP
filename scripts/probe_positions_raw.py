"""
探針:allPosition 為什麼回空 —— 而 App 上明明有倉 · 2026-09-19

═══ 對不上的事實 ═══
2026-09-19 17:21,執政官的 App:

    持倉 (3)   當前委託 (0)
    BTCUSDT 做多 5x 開倉均價 81,405.58 持倉 0.0122
    BNBUSDT 做多 5x 開倉均價 768.3227 持倉 392.23
    (第三筆)
    帳戶資產 120,850.5   未實現盈虧 +88.35

同一時間,`/openApi/contract/v1/allPosition`(不帶參數)回的是**空的**,
所以面板一直說「交易所端 0 筆持倉」。

**那是一句假話,而且是最危險的一種**:對齊單會據此叫人去開一個
他已經持有的倉。系統不是不知道,是它以為自己知道。

═══ 上一支探針的缺陷(我的錯)═══
`probe_stop_visibility.py` 看到空清單就印「目前沒有持倉」——
那是**結論**,不是證據。code 是多少?msg 說了什麼?data 是 null、
是 []、還是一個 dict?那支沒印,所以什麼都排除不掉。

這支只做一件事:**把原始信封原封不動貼出來。**

═══ 要分辨的幾種可能 ═══
一、要帶 symbol 才給(allOrders 就是必填 symbol)
二、代號格式不對(BTCUSDT vs BTC-USDT —— allOrders 兩種都試過)
三、這個端點對模擬金(VST)不回東西
四、真的就是空的,而 App 顯示的是別的產品

四種的處置完全不同,所以不能用猜的。
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

#: 執政官 2026-09-19 螢幕上真的有倉的那幾個。
SEEN_IN_APP = ("BTCUSDT", "BNBUSDT")


def show(label: str, params: dict | None) -> None:
    from exchange.bingx.private import READ_ONLY, PrivateCallFailed
    from exchange.bingx.standard import BingXStandardUSDT

    print(f"\n{LINE}\n  {label}\n  params = {params!r}\n{LINE}")
    api = BingXStandardUSDT()
    try:
        raw = api._c().get(READ_ONLY["std_positions"], params)
    except PrivateCallFailed as e:
        print(f"  呼叫失敗:{e}")
        return
    except Exception as e:                           # noqa: BLE001
        print(f"  呼叫失敗:{type(e).__name__}: {e}")
        return

    # get() 可能已經剝掉信封 —— 兩種都印,不假設。
    print(f"  回傳型別:{type(raw).__name__}")
    txt = json.dumps(raw, ensure_ascii=False, default=str)
    print(f"  原樣(前 1200 字):\n    {txt[:1200]}")
    if isinstance(raw, list):
        print(f"  → 陣列,{len(raw)} 筆")
        for row in raw[:4]:
            if isinstance(row, dict):
                print(f"    keys = {sorted(row)}")
    elif isinstance(raw, dict):
        print(f"  → 物件,keys = {sorted(raw)}")


def main() -> int:
    print(f"\n{LINE}\n  allPosition 原始回應 —— 不下判斷,只貼證據\n{LINE}")
    print("  對照:2026-09-19 17:21 App 上是「持倉 (3)」,")
    print("        而面板說「交易所端 0 筆持倉」。至少有一邊是錯的。")

    show("一、不帶任何參數(現在程式在做的)", None)
    for sym in SEEN_IN_APP:
        show(f"二、帶 symbol = {sym}(無槓,allOrders 認得這種)",
             {"symbol": sym})
    base = SEEN_IN_APP[0]
    dashed = base.replace("USDT", "-USDT")
    show(f"三、帶 symbol = {dashed}(有槓,官方文件寫這種)",
         {"symbol": dashed})

    # 餘額當對照組:同一把金鑰、同一個 contract/v1、同一種簽章。
    print(f"\n{LINE}\n  四、對照組:balance(同一個產品、同一把金鑰)\n{LINE}")
    try:
        from exchange.bingx.standard import BingXStandardUSDT
        bal = BingXStandardUSDT().balance()
        print(f"  {json.dumps(bal, ensure_ascii=False, default=str)[:600]}")
        print("  → 餘額有回東西的話,證明金鑰/簽章/產品路徑都是通的,")
        print("    那 allPosition 回空就**不是權限問題**。")
    except Exception as e:                           # noqa: BLE001
        print(f"  問不到:{type(e).__name__}: {e}")

    print(f"\n{LINE}\n  怎麼看這份輸出\n{LINE}")
    print("""
  · 不帶 symbol 空、帶 symbol 有 -> 端點**要求 symbol**。
    那表示我們得先知道有哪些幣才問得到持倉,而這個產品沒有
    contracts 端點 —— 要嘛掃現役七幣,要嘛從 allOrders 反推。

  · 兩種代號格式只有一種有 -> 跟 allOrders 一樣的格式問題。

  · 全部都空、而 balance 有回東西 -> 不是權限、不是簽章。
    那就是這個端點對模擬金(VST)不給持倉,
    **而那是一個必須寫在面板上的洞**:交易所端的持倉我們看不到,
    所以「0 筆持倉」這句話永遠不能講。

  · code 不是 0 -> 把 code 與 msg 一起貼回來,不要自己解讀
    (100400 = 沒有這個 handler;104414 = 參數錯誤,端點是在的)
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
