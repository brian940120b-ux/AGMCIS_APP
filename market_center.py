"""
市場總覽。

Phase 7 修掉兩個問題:

  1. **資料來源是 Binance,實際交易在 BingX。** 兩邊的價格、成交量與
     漲跌幅都不一樣,用 Binance 的數字判斷「該做多哪個幣」等於在看別人的盤。
     現在走 agmcis.data.market_data(BingX 永續合約)。

  2. **抓不到資料時塞 0 進去。** 那會讓漲跌幅 0%、成交量 0 被當成真實數據
     參與排序與情緒計算 —— 交易所掛掉時系統會回報「中性」,
     而不是「我不知道」。這正是 Phase 0 稽核抓到的「分不出市場中性與資料壞掉」。
     現在抓不到就標記 available=False,不參與排序,
     可用資料不足時 market_sentiment 直接回 "資料不足"。
"""
import logging

from agmcis.config import settings
from agmcis.data import market_data

logger = logging.getLogger("agmcis.market_center")

SYMBOLS = list(settings.WATCHLIST_SYMBOLS)

# 至少要有這個比例的標的取得到行情,才敢對整體市場下判斷
MIN_AVAILABLE_RATIO = 0.6

SENTIMENT_UNKNOWN = "資料不足"


def _fetch_one(symbol):
    ticker = market_data.get_ticker(symbol)

    if not ticker or ticker.get("price") in (None, 0):
        logger.warning("market_center 取不到 %s 的行情,標記為不可用", symbol)
        return {
            "symbol": symbol,
            "available": False,
            "price": None,
            "change_24h": None,
            "volume_24h": None,
        }

    # adapter 回的欄位名是 change_pct_24h / quote_volume_24h,
    # 但對外仍沿用舊的 change_24h / volume_24h,避免改到模板與呼叫端。
    change = ticker.get("change_pct_24h")
    volume = ticker.get("quote_volume_24h")
    if volume is None:
        volume = ticker.get("volume_24h")

    return {
        "symbol": symbol,
        "available": True,
        "price": round(float(ticker["price"]), 6),
        "change_24h": round(float(change), 2) if change is not None else None,
        "volume_24h": round(float(volume), 2) if volume is not None else None,
    }


def get_market_center(symbols=None):
    symbols = list(symbols) if symbols else SYMBOLS
    market = [_fetch_one(symbol) for symbol in symbols]

    usable = [item for item in market if item["available"]
              and item["change_24h"] is not None]

    # 排序只用拿得到資料的標的。把 None 當 0 排序會讓故障的標的
    # 憑空排在下跌標的前面。
    gainers = sorted(usable, key=lambda x: x["change_24h"], reverse=True)
    volume_rank = sorted(
        [item for item in usable if item["volume_24h"] is not None],
        key=lambda x: x["volume_24h"], reverse=True,
    )

    enough_data = symbols and len(usable) >= len(symbols) * MIN_AVAILABLE_RATIO

    if not enough_data:
        logger.error(
            "market_center 只取得 %d/%d 個標的的行情,不下市場判斷",
            len(usable), len(symbols),
        )
        return {
            "market_data": market,
            "gainers": gainers,
            "volume_rank": volume_rank,
            "best_symbol": None,
            "market_sentiment": SENTIMENT_UNKNOWN,
            "market_score": None,
            "available_count": len(usable),
            "requested_count": len(symbols),
        }

    positive = len([item for item in usable if item["change_24h"] > 0])
    ratio = positive / len(usable)

    if ratio >= 0.7:
        sentiment, score = "偏多", 80
    elif ratio >= 0.4:
        sentiment, score = "中性", 50
    else:
        sentiment, score = "偏空", 25

    return {
        "market_data": market,
        "gainers": gainers,
        "volume_rank": volume_rank,
        "best_symbol": gainers[0]["symbol"] if gainers else None,
        "market_sentiment": sentiment,
        "market_score": score,
        "available_count": len(usable),
        "requested_count": len(symbols),
    }
