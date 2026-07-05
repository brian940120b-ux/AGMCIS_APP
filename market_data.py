"""
Market Data Service — BingX 合約專用

對外函式介面跟原版完全相容(get_price / get_ohlcv / get_exchange_for_symbol),
所有呼叫端完全不用改。內部保留原本的快取設計,並加強「API 失敗時退回舊值」的容錯。
"""
import time
import pandas as pd

from exchange_engine import get_price_safe, fetch_ohlcv_safe, get_exchange
from logger_service import logger

PRICE_CACHE = {}
PRICE_CACHE_TTL = 1

OHLCV_CACHE = {}
OHLCV_CACHE_TTL = 15


def get_exchange_for_symbol(symbol):
    exchange = get_exchange("bingx")
    return ("bingx", exchange) if exchange else (None, None)


def get_ohlcv(symbol="BTC/USDT", timeframe="1h", limit=150):
    now = time.time()
    cache_key = f"{symbol}:{timeframe}:{limit}"
    cached = OHLCV_CACHE.get(cache_key)

    if cached:
        df, ts = cached
        if now - ts < OHLCV_CACHE_TTL:
            return df

    result = fetch_ohlcv_safe(symbol, timeframe, limit)

    if result is None:
        logger.warning(f"{symbol} OHLCV not found on BingX")
        if cached:
            return cached[0]
        return None

    try:
        name = result["exchange"]
        data = result["ohlcv"]

        df = pd.DataFrame(
            data,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["exchange"] = name

        OHLCV_CACHE[cache_key] = (df, now)
        return df

    except Exception as e:
        logger.error(f"{symbol} get_ohlcv error on bingx: {e}")
        if cached:
            return cached[0]
        return None


def get_price(symbol):
    now = time.time()

    cached = PRICE_CACHE.get(symbol)
    if cached:
        price, ts = cached
        if now - ts < PRICE_CACHE_TTL:
            return price

    result = get_price_safe(symbol)

    if result is None or result.get("price") is None:
        if cached:
            return cached[0]
        return None

    price = float(result["price"])
    PRICE_CACHE[symbol] = (price, now)
    return price
