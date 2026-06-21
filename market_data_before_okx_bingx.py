import ccxt
import pandas as pd

exchange = ccxt.binance()


def get_ohlcv(symbol="BTC/USDT", timeframe="1h", limit=150):
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

    return df


def get_price(symbol):
    df = get_ohlcv(symbol, limit=5)

    if df is None:
        return None

    if len(df) == 0:
        return None

    return float(df["close"].iloc[-1])