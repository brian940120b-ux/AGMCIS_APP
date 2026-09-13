"""
新系統 — 交易池篩選 · 2026-09-09

═══ 為什麼要有這一支 ═══
交易池原本是寫死的 7 個幣。那 7 個是回測驗證過的籃子,但它們怎麼來的?
「今天還活著、而且有 3.3 年歷史的幣」—— 這是倖存者偏誤,CLAUDE.md
一直誠實記著這件事。執政官要求改成由系統從 BingX 全部合約裡篩。

═══ 只用結構性條件,不用預測性條件 ═══
這是這一支能不能存在的關鍵分界:

  結構性(可以用)—— 它們是「其他東西成立的前提」,不是策略選擇
    · 流動性門檻:成本模型(滑點 0.02%)是在深盤口實測的。實測漲幅榜
      的幣成交額只有現役幣的 1/79,我們一張 700 USDT 的單會佔到日成交
      量的 0.24%(BTC 是 0.0001%)—— 市場衝擊差 2,400 倍。
      不設門檻,等於把成本對齊的工作一次作廢。
    · 歷史長度:算不出 50 日均線的幣,策略根本無法運作。
    · 交易所狀態:下架的幣不能下單。

  預測性(不可以用)—— 每一個都是自由參數,是過擬合的入口
    · 「漲最多的前 N 個」「動能最強的」「離均線最遠的」
    · 2026-09-08 已實測過一次「按離均線距離取前一半」——**輸給等權
      持有全部**,原因是集中度傷害分散。那次的教訓現在寫在這裡。

═══ 誠實揭露:倖存者偏誤沒有被解決 ═══
本篩選用**當下**的成交額,所以選出來的是「今天流動性夠」的幣。
拿今天的資訊去挑 2023 年的標的,偏誤依然存在,只是從 7 個變成 21 個 ——
**不是變糟,但也沒有變好**。真正的解法是逐時點篩選(每一天只用該日
之前的成交量),資料上做得到,但那是另一件工程,尚未實作。
任何用本模組跑出來的回測數字,都必須連著這一段一起讀。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.atomic import write_json_atomic
from core.logging import get_logger

log = get_logger("portfolio.universe")

BASE = Path(__file__).resolve().parents[1]
SNAPSHOT = BASE / "data" / "universe.json"
TICKER_EP = "https://open-api.bingx.com/openApi/swap/v2/quote/ticker"
KLINE_EP = "https://open-api.bingx.com/openApi/swap/v3/quote/klines"

# ── 篩選門檻(2026-09-09 訂,測試前寫下)────────────────────
# 兩個數字都不是搜出來的,各有結構性理由:
#   MIN_QUOTE_VOLUME_USDT 1e7 —— 現役 7 幣的最低成交額是 2,690 萬,
#     取 1,000 萬是「比現役最低再寬一級」,不是為了湊出好看的幣數。
#     實測結果:1,000 萬 → 46 個幣、2,500 萬 → 12 個、5,000 萬 → 6 個
#     (5,000 萬會把現役 4 個幣踢掉,顯然過嚴)。
#   MIN_DAILY_BARS 1000 —— BingX 日線端點的上限就是 1000 根,
#     取滿是為了讓所有入選幣有**相同長度**的歷史,回測才公平。
MIN_QUOTE_VOLUME_USDT = 1e7
MIN_DAILY_BARS = 1000
# ─────────────────────────────────────────────────────────


def _get(url: str, timeout: int = 15):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "agmcis/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def screen(min_volume: float = MIN_QUOTE_VOLUME_USDT,
           min_bars: int = MIN_DAILY_BARS,
           verbose: bool = False) -> list[str]:
    """回傳通過結構性篩選的幣種,依成交額由大到小。

    每一關淘汰多少都記在 log 裡 —— 篩選過程必須看得見,
    不然「為什麼這個幣不在裡面」就變成無法回答的問題。
    """
    from portfolio import specs

    d = _get(TICKER_EP)
    if str(d.get("code")) != "0":
        raise RuntimeError(f"BingX ticker code={d.get('code')} {d.get('msg')}")
    tick = {}
    for t in (d.get("data") or []):
        try:
            tick[t["symbol"]] = float(t["quoteVolume"])
        except (KeyError, TypeError, ValueError):
            continue
    n0 = len(tick)

    # 一、交易所狀態必須可交易
    cand = [s for s in tick if _tradable(s)]
    n1 = len(cand)

    # 二、流動性門檻
    cand = [s for s in cand if tick[s] >= min_volume]
    n2 = len(cand)

    # 三、歷史長度(逐幣查,慢但只在篩選時跑)
    out = []
    for s in sorted(cand, key=lambda x: -tick[x]):
        try:
            k = _get(f"{KLINE_EP}?symbol={s}&interval=1d&limit=1440", timeout=12)
            if len(k.get("data") or []) >= min_bars:
                out.append(s)
        except Exception as e:
            log.warning(f"{s} 日線查詢失敗,排除:{e}")
        time.sleep(0.1)          # 對交易所客氣一點
    n3 = len(out)

    log.info(f"交易池篩選:{n0} 個合約 → 可交易 {n1} → "
             f"成交額≥{min_volume:,.0f} 剩 {n2} → 歷史≥{min_bars} 天 剩 {n3}")
    if verbose:
        print(f"  全部合約        {n0}")
        print(f"  狀態可交易      {n1}")
        print(f"  成交額 ≥{min_volume:,.0f}  {n2}")
        print(f"  歷史 ≥{min_bars} 天    {n3}")
    return out


def _tradable(symbol: str) -> bool:
    from portfolio import specs
    try:
        return specs.tradable(symbol)
    except specs.SpecMissing:
        return False


def save_snapshot(symbols: list[str]) -> None:
    """把篩選結果與當下的門檻一起落地。

    落地的理由:回測跑完之後,「當時是用哪一組幣、哪一組門檻」必須
    查得到。不然幾天後看到一個數字,沒有人能重現它。
    """
    write_json_atomic(SNAPSHOT, {
        "updated": time.time(),
        "min_quote_volume_usdt": MIN_QUOTE_VOLUME_USDT,
        "min_daily_bars": MIN_DAILY_BARS,
        "symbols": symbols,
        "n": len(symbols),
    })


def load_snapshot() -> list[str]:
    try:
        return list(json.loads(SNAPSHOT.read_text(encoding="utf-8"))["symbols"])
    except Exception:
        return []


def main() -> int:
    syms = screen(verbose=True)
    save_snapshot(syms)
    print(f"\n通過篩選 {len(syms)} 個幣:")
    for i, s in enumerate(syms, 1):
        print(f"  {i:>2}. {s}")
    print(f"\n已落地 → {SNAPSHOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
