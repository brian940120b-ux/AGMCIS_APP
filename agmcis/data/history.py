"""
分頁抓取歷史 K 棒。

## 為什麼需要

交易所單次請求給的 K 棒有上限(BingX 約 1000-1500 根)。以 1h 為例,
1500 根只有 62 天;切掉 30% 樣本外之後剩 450 根,而 live 訊號管線
在那段時間裡大約只交易 28 筆 —— **剛好卡在 Phase 8 的 30 筆門檻底下**。

也就是說:現在擋住驗證的不是策略不好,是**樣本不夠**。
要拿到足夠的樣本就必須分頁抓。

## 三件必須做對的事

1. **缺口要偵測出來,不能靜靜接起來。**
   兩段之間少了 12 小時,直接串起來會讓回測看到一根「12 小時內漲 3%」
   的假 K 棒 —— 那個價格跳動從來沒有發生過,但停損會照著它被觸發。

2. **重複的時間戳要去掉。** 分頁的邊界常常重疊。同一根算兩次會讓
   指標與交易筆數都失真。

3. **快取到磁碟。** Lab 會重跑很多次(不同參數、不同時間框架)。
   每次都重抓一遍既慢又浪費限流額度,而歷史 K 棒是不會變的。

## 不做的事

**不補洞。** 缺口就是缺口,標記出來讓呼叫端決定 ——
用前一根的收盤價填進去,會製造出一段「什麼都沒發生」的假歷史,
而那段假歷史會被當成真的拿去算勝率。
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List

from agmcis.core.enums import MarketType
from agmcis.data.quality import TIMEFRAME_MS

logger = logging.getLogger("agmcis.data.history")

CACHE_DIR = os.getenv("HISTORY_CACHE_DIR", "data/history")

# 單次請求抓幾根。交易所的上限依端點而異,取一個保守值。
PAGE_SIZE = 1000

# 最多打幾次 API。防止時間框架或參數算錯時無限迴圈。
MAX_PAGES = 50

# 兩次請求之間的間隔。限流器會再擋一層,這只是禮貌。
PAGE_DELAY_SECONDS = 0.2


@dataclass
class HistoryResult:
    symbol: str
    timeframe: str
    candles: List[List] = field(default_factory=list)
    gaps: List[Dict] = field(default_factory=list)
    duplicates_removed: int = 0
    pages_fetched: int = 0
    from_cache: bool = False

    @property
    def count(self):
        return len(self.candles)

    @property
    def has_gaps(self):
        return bool(self.gaps)

    @property
    def span_days(self):
        if len(self.candles) < 2:
            return 0.0
        return (self.candles[-1][0] - self.candles[0][0]) / 86_400_000

    def to_dict(self):
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "count": self.count,
            "span_days": round(self.span_days, 2),
            "gaps": list(self.gaps),
            "duplicates_removed": self.duplicates_removed,
            "pages_fetched": self.pages_fetched,
            "from_cache": self.from_cache,
        }

    def summary_lines(self):
        lines = [
            f"{self.symbol} {self.timeframe}:{self.count} 根 / "
            f"{self.span_days:.1f} 天"
            + ("(來自快取)" if self.from_cache else f"(抓了 {self.pages_fetched} 頁)")
        ]
        if self.duplicates_removed:
            lines.append(f"  去除重複 {self.duplicates_removed} 根")
        for gap in self.gaps[:5]:
            lines.append(
                f"  ⚠️  缺口:{gap['missing']} 根 "
                f"({gap['from']} -> {gap['to']})"
            )
        if len(self.gaps) > 5:
            lines.append(f"  ⚠️  還有 {len(self.gaps) - 5} 個缺口")
        return lines


def _dedupe(rows):
    """依時間戳去重並排序。回傳 (K 棒, 移除幾根)。"""
    seen = {}
    for row in rows:
        if not row:
            continue
        seen[int(row[0])] = row

    ordered = [seen[key] for key in sorted(seen)]
    return ordered, max(0, len(rows) - len(ordered))


def find_gaps(candles, timeframe):
    """
    找出時間序列裡的缺口。

    容忍一根的誤差 —— 交易所偶爾會漏一根,那不值得讓整段資料作廢。
    連續缺兩根以上才算缺口。
    """
    interval = TIMEFRAME_MS.get(timeframe)
    if not interval or len(candles) < 2:
        return []

    gaps = []
    for current, following in zip(candles, candles[1:]):
        delta = following[0] - current[0]
        missing = round(delta / interval) - 1

        if missing >= 2:
            gaps.append({
                "from": int(current[0]),
                "to": int(following[0]),
                "missing": int(missing),
            })

    return gaps


def _cache_path(symbol, timeframe, market_type):
    safe = symbol.replace("/", "_").replace(":", "-")
    return os.path.join(CACHE_DIR, f"{safe}_{timeframe}_{market_type}.json")


def _load_cache(path, minimum):
    if not os.path.exists(path):
        return None

    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        # 壞掉的快取不可以被當成「沒有快取」然後靜靜覆蓋掉 ——
        # 但它也不該擋住抓取。記錄下來,重抓。
        logger.warning("歷史快取讀取失敗,將重新抓取:%s | %s", path, exc)
        return None

    candles = data.get("candles") or []
    if len(candles) < minimum:
        return None

    return candles


def _save_cache(path, symbol, timeframe, candles):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "w", encoding="utf-8") as handle:
        json.dump({
            "symbol": symbol,
            "timeframe": timeframe,
            "count": len(candles),
            "candles": candles,
        }, handle)


def fetch(symbol, timeframe="1h", total=5000,
          market_type=MarketType.PERPETUAL, adapter=None,
          use_cache=True, sleep=time.sleep):
    """
    抓 `total` 根 K 棒,必要時分多次請求。

    往**回**抓:從最近的一根開始,每次用最舊那一根的時間往前要更多。
    這樣拿到的一定是「最近的 N 根」,而不是某段隨機的歷史。
    """
    interval = TIMEFRAME_MS.get(timeframe)
    if not interval:
        raise ValueError(f"不認得的時間框架:{timeframe}")

    market_type = MarketType.parse(market_type, MarketType.PERPETUAL)
    path = _cache_path(symbol, timeframe, market_type.value)

    if use_cache:
        cached = _load_cache(path, total)
        if cached:
            candles = cached[-total:]
            return HistoryResult(
                symbol=symbol, timeframe=timeframe, candles=candles,
                gaps=find_gaps(candles, timeframe), from_cache=True,
            )

    if adapter is None:
        from agmcis.data.market_data import get_adapter
        adapter = get_adapter()

    collected = []
    oldest = None
    pages = 0

    while len(collected) < total and pages < MAX_PAGES:
        if oldest is None:
            rows = adapter.get_ohlcv(
                symbol, timeframe, limit=PAGE_SIZE, market_type=market_type,
            )
        else:
            # 再往前要一頁。since 用「最舊那根往前 PAGE_SIZE 根」的時間。
            since = int(oldest - PAGE_SIZE * interval)
            rows = _fetch_since(adapter, symbol, timeframe, since,
                                market_type, PAGE_SIZE)

        pages += 1

        if not rows:
            logger.info(
                "歷史抓取 | %s %s | 第 %s 頁沒有資料,停止(已有 %s 根)",
                symbol, timeframe, pages, len(collected),
            )
            break

        previous_oldest = oldest
        collected.extend(rows)
        oldest = min(int(row[0]) for row in rows)

        # 交易所不再給更早的資料時會一直回同一段。
        # 沒有往前推進就停 —— 否則會打滿 MAX_PAGES 次白工。
        if previous_oldest is not None and oldest >= previous_oldest:
            logger.info(
                "歷史抓取 | %s %s | 已經抓到最早的資料(%s 根)",
                symbol, timeframe, len(collected),
            )
            break

        if len(collected) < total:
            sleep(PAGE_DELAY_SECONDS)

    candles, duplicates = _dedupe(collected)
    candles = candles[-total:]

    result = HistoryResult(
        symbol=symbol, timeframe=timeframe, candles=candles,
        gaps=find_gaps(candles, timeframe),
        duplicates_removed=duplicates, pages_fetched=pages,
    )

    if result.has_gaps:
        # 缺口不補。串起來會讓回測看到一根從來沒發生過的價格跳動,
        # 而停損會照著它被觸發。
        logger.warning(
            "歷史抓取 | %s %s | 有 %s 個缺口,**沒有補值** | 最大缺口 %s 根",
            symbol, timeframe, len(result.gaps),
            max(g["missing"] for g in result.gaps),
        )

    if use_cache and candles:
        _save_cache(path, symbol, timeframe, candles)

    return result


def _fetch_since(adapter, symbol, timeframe, since, market_type, limit):
    """
    帶 since 的抓取。adapter 不支援時回空 list ——
    **不要退回不帶 since 的請求**,那會一直拿到同一段最近的資料,
    看起來像是在分頁,實際上原地打轉。
    """
    try:
        exchange = adapter._instance(market_type)
        market_symbol = adapter.to_market_symbol(symbol, market_type)
        return exchange.fetch_ohlcv(market_symbol, timeframe, since, limit) or []
    except Exception as exc:
        logger.warning(
            "歷史抓取 | %s %s | since=%s 請求失敗:%s", symbol, timeframe, since, exc,
        )
        return []


def to_frame(result):
    """轉成回測與 Lab 用的 DataFrame。"""
    import pandas as pd

    return pd.DataFrame(
        result.candles,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
