from market_data import get_price, get_ohlcv
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator

def get_indicators(symbol):
    """
    V77.3 第一版
    目前提供框架，之後會接 RSI、MACD、EMA 等真實計算。
    """
    price = get_price(symbol)
    rsi = None
    ema20 = None
    ema60 = None
    trend = "UNKNOWN"

    try:
        df = get_ohlcv(symbol, "1h", 100)
        rsi = round(float(RSIIndicator(df["close"], window=14).rsi().iloc[-1]), 2)
        ema20 = round(float(EMAIndicator(df["close"], window=20).ema_indicator().iloc[-1]), 4)
        ema60 = round(float(EMAIndicator(df["close"], window=60).ema_indicator().iloc[-1]), 4)

        trend = "BULLISH" if ema20 > ema60 else "BEARISH"
    except Exception:
        rsi = None
    ema20 = None
    ema60 = None
    trend = "UNKNOWN"

    return {
        "symbol": symbol,
        "price": price,
        "rsi": rsi,
        "macd": None,
        "ema20": ema20,
        "ema60": ema60,
        "trend": trend,
        "signal": "HOLD"
    }
