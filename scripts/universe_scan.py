"""
交易池掃描 —— 篩選機制,跑給你看 · 2026-09-19

執政官:「有一種篩選機制,其他人都做得出來,為什麼你做不出來?」

**做出來過,壞掉了,被退役,而我沒把它接回來。**

2026-09-13 的測試組跑動態交易池,篩進 1000PEPE-USDT,而沒有人抓過
那個幣的資金費歷史:

    SpecMissing: 沒有 1000PEPE-USDT 的資金費率歷史 —— 不猜一個數字

那個例外是**對的**(用不完整的資料記帳會靜靜少收資金費、美化績效)。
錯的是沒有人先去抓歷史,而且整個 tick 跟著死:73.7 小時沒有記帳。

退役是當時對的決定。但之後沒有人把那個 bug 修掉再把它接回來,
而我一路用「那是策略層的決定」把它推掉 —— **它不是策略決定,
是一個沒修的 bug。**

═══ 這支做什麼 ═══
把整個 BingX 永續市場掃一遍,印出每一關淘汰了多少、剩下誰。
不寫帳本、不改設定 —— **只是讓你看見篩選在動。**

四道關卡,全部是**結構性**條件,沒有一條是預測性的:

  一、交易所狀態可交易       下架的、暫停的,不碰
  二、24h 成交額 ≥ 1,000 萬   流動性不足 = 滑點吃掉一切
  三、日線 ≥ 1000 根          歷史不夠長就回測不了,回測不了就不知道
  四、資金費歷史抓得到        **這一關是 73.7 小時那次的直接教訓**

⚠️ 沒有「漲最多的前 N 個」那種條件。2026-09-08 實測過一次「按離均線
排序挑」,那是預測性篩選,而它在驗證段輸給隨機。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import interpreter

interpreter.require()

LINE = "═" * 64


def main() -> int:
    from portfolio import universe
    from portfolio.paper import SYMBOLS, screened_universe

    print(f"\n{LINE}\n  交易池掃描 —— 全 BingX 永續市場\n{LINE}")
    print(f"  門檻:24h 成交額 ≥ {universe.MIN_QUOTE_VOLUME_USDT:,.0f} USDT")
    print(f"        日線 ≥ {universe.MIN_DAILY_BARS} 根")
    print("  全部是結構性條件 —— 沒有一條在預測漲跌。\n")
    print("  ⚠️ 第三關會逐幣查日線,幾百個幣要跑幾分鐘。請等它。\n")

    try:
        picked = universe.screen(verbose=True)
    except Exception as e:                           # noqa: BLE001
        print(f"\n  ✗ 篩選失敗:{type(e).__name__}: {e}")
        return 1

    print(f"\n{LINE}\n  過前三關的 {len(picked)} 個\n{LINE}")
    for i in range(0, len(picked), 6):
        print("  " + "  ".join(f"{s:<16}" for s in picked[i:i + 6]))

    print(f"\n{LINE}\n  第四關:資料齊全到可以誠實記帳嗎\n{LINE}")
    print("  (逐幣補抓資金費歷史 —— 這一關是 73.7 小時那次的教訓)\n")
    final = screened_universe()

    print(f"\n{LINE}\n  結果\n{LINE}")
    print(f"  過前三關      {len(picked)}")
    print(f"  資料齊全      {len(final)}")
    now = set(SYMBOLS)
    new = [s for s in final if s not in now]
    gone = [s for s in now if s not in final]
    print(f"\n  主城現在跑的  {len(now)} 個:{', '.join(sorted(now))}")
    if new:
        print(f"\n  篩選會**加進來** {len(new)} 個:")
        for i in range(0, len(new), 6):
            print("    " + "  ".join(f"{s:<16}" for s in new[i:i + 6]))
    if gone:
        print(f"\n  篩選會**刷掉** {len(gone)} 個:{', '.join(sorted(gone))}")
        print("    (通常是成交額掉下門檻,或歷史不夠長)")
    if not new and not gone:
        print("\n  篩選結果與主城現在跑的**完全一樣**。")

    print(f"""
{LINE}
  接下來會發生什麼
{LINE}

  測試組(SCREENED)從今天起**每天自己記帳一次**,用的就是上面
  這份篩出來的池子,獨立帳本、獨立權益曲線,跟主城平行跑。

  主城**不會**被改動。動態交易池好不好,由前向資料自己回答 ——
  不是由一次回測回答,也不是由我講。兩邊用同一段真實價格、同一份
  plan()/tick(),所以結構上不可能分岔。

  要看結果:面板的「模擬帳戶」卡,或

      python -c "from portfolio.scorecard import score, Live; \\
                 from portfolio.paper import SCREENED; \\
                 print(score(SCREENED.curve_path).verdict)"

  ⚠️ 十天的資料回答不了這個問題。成績單會說「還不知道」,
     而那是正確答案,不是故障。
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
