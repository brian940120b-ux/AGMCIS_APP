import time
import ccxt
import pandas as pd
from exchange_engine import get_price_safe, fetch_ohlcv_safe
from logger_service import logger

EXCHANGES = {
    "okx": ccxt.okx(),
    "bingx": ccxt.bingx()
}

# 1 秒價格快取
PRICE_CACHE = {}
PRICE_CACHE_TTL = 1


def get_exchange_for_symbol(symbol):
    for name, exchange in EXCHANGES.items():
        try:
            markets = exchange.load_markets()
            if symbol in markets:
                return name, exchange
        except Exception as e:
            logger.error(f"{name} load_markets error: {e}")
    return None, None


def get_ohlcv(symbol="BTC/USDT", timeframe="1h", limit=150):
    result = fetch_ohlcv_safe(symbol, timeframe, limit)

    if result is None:
        logger.warning(f"{symbol} OHLCV not found on OKX/BingX")
        return None

    try:
        name = result["exchange"]
        data = result["ohlcv"]

        df = pd.DataFrame(
            data,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume"
            ]
        )

        df["exchange"] = name
        return df

    except Exception as e:
        logger.error(f"{symbol} get_ohlcv error on {name}: {e}")
        return None


def get_price(symbol):
    now = time.time()

    # 先查快取
    cached = PRICE_CACHE.get(symbol)
    if cached:
        price, ts = cached
        if now - ts < PRICE_CACHE_TTL:
            return price

    # 查最新價格（OKX→BingX 自動備援）
    result = get_price_safe(symbol)

    if result is None:
        if cached:
            return cached[0]
        return None

    price = float(result["price"])

    # 更新快取
    PRICE_CACHE[symbol] = (price, now)

    return price
