from market_data import get_price, get_ohlcv
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import AverageTrueRange

def get_indicators(symbol):
    """
    V77.3 第一版
    目前提供框架，之後會接 RSI、MACD、EMA 等真實計算。
    """
    price = get_price(symbol)
    rsi = None
    ema20 = None
    ema60 = None
    macd = None
    macd_signal = None
    macd_hist = None
    atr = None
    trend = "UNKNOWN"

    try:
        df = get_ohlcv(symbol, "1h", 100)
        rsi = round(float(RSIIndicator(df["close"], window=14).rsi().iloc[-1]), 2)
        ema20 = round(float(EMAIndicator(df["close"], window=20).ema_indicator().iloc[-1]), 4)
        ema60 = round(float(EMAIndicator(df["close"], window=60).ema_indicator().iloc[-1]), 4)

        macd_obj = MACD(df["close"])
        macd = round(float(macd_obj.macd().iloc[-1]),4)
        macd_signal = round(float(macd_obj.macd_signal().iloc[-1]),4)
        macd_hist = round(float(macd_obj.macd_diff().iloc[-1]),4)

        atr = round(float(
            AverageTrueRange(
                high=df["high"],
                low=df["low"],
                close=df["close"],
                window=14
            ).average_true_range().iloc[-1]
        ),4)

        trend = "BULLISH" if ema20 > ema60 else "BEARISH"
    except Exception:
        pass

    return {
        "symbol": symbol,
        "price": price,
        "rsi": rsi,
        "macd": macd,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "atr": atr,
        "ema20": ema20,
        "ema60": ema60,
        "trend": trend,
        "signal": "HOLD"
    }
