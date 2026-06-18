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