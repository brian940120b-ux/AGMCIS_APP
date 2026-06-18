import ccxt
import pandas as pd

from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange

exchange = ccxt.binance()


def load_data(symbol):
    data = exchange.fetch_ohlcv(
        symbol,
        timeframe="1h",
        limit=1500
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

    close = df["close"]

    df["ema20"] = EMAIndicator(close=close, window=20).ema_indicator()
    df["ema50"] = EMAIndicator(close=close, window=50).ema_indicator()
    df["rsi"] = RSIIndicator(close=close, window=14).rsi()

    macd = MACD(close=close)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()

    adx = ADXIndicator(
        high=df["high"],
        low=df["low"],
        close=df["close"],
        window=14
    )
    df["adx"] = adx.adx()

    atr = AverageTrueRange(
        high=df["high"],
        low=df["low"],
        close=df["close"],
        window=14
    )
    df["atr"] = atr.average_true_range()

    df["vol_ma20"] = df["volume"].rolling(20).mean()

    return df


def run_strategy(df, strategy_module):
    capital = 10000

    in_position = False
    entry_price = 0

    for i in range(60, len(df)):
        price = float(df["close"].iloc[i])
        ema20 = float(df["ema20"].iloc[i])
        ema50 = float(df["ema50"].iloc[i])
        rsi = float(df["rsi"].iloc[i])
        macd = float(df["macd"].iloc[i])
        macd_signal = float(df["macd_signal"].iloc[i])
        volume = float(df["volume"].iloc[i])
        vol_ma = float(df["vol_ma20"].iloc[i])
        atr = float(df["atr"].iloc[i])
        adx = float(df["adx"].iloc[i])

        if (
            not in_position
            and strategy_module.buy_signal(
                price,
                ema20,
                ema50,
                rsi,
                macd,
                macd_signal,
                volume,
                vol_ma,
                atr,
                adx
            )
        ):
            in_position = True
            entry_price = price

        elif (
            in_position
            and strategy_module.sell_signal(
                price,
                ema20,
                ema50,
                rsi,
                macd,
                macd_signal,
                volume,
                vol_ma,
                atr,
                adx
            )
        ):
            pnl = (price - entry_price) / entry_price

            capital *= (1 + pnl)

            in_position = False

    return capital