import time
import ccxt
import pandas as pd
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
    name, exchange = get_exchange_for_symbol(symbol)

    if exchange is None:
        logger.warning(f"{symbol} not found on OKX/BingX")
        return None

    try:
        data = exchange.fetch_ohlcv(
            symbol,
            timeframe=timeframe,
            limit=limit
        )

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

    # 查最新價格
    df = get_ohlcv(symbol, limit=1)

    # 若查詢失敗，回傳舊快取
    if df is None or len(df) == 0:
        if cached:
            return cached[0]
        return None

    price = float(df["close"].iloc[-1])

    # 更新快取
    PRICE_CACHE[symbol] = (price, now)

    return price
