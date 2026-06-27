import ccxt
import pandas as pd

EXCHANGES = {
    "okx": ccxt.okx(),
    "bingx": ccxt.bingx()
}


def get_exchange_for_symbol(symbol):
    for name, exchange in EXCHANGES.items():
        try:
            markets = exchange.load_markets()
            if symbol in markets:
                return name, exchange
        except Exception as e:
            print(f"{name} load_markets error: {e}")
    return None, None


def get_ohlcv(symbol="BTC/USDT", timeframe="1h", limit=150):
    name, exchange = get_exchange_for_symbol(symbol)

    if exchange is None:
        print(f"{symbol} not found on OKX/BingX")
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
        print(f"{symbol} get_ohlcv error on {name}: {e}")
        return None


def get_price(symbol):
    df = get_ohlcv(symbol, limit=1)

    if df is None or len(df) == 0:
        return None

    return float(df["close"].iloc[-1])
