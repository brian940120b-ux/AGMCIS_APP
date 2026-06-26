from market_data import get_price, get_ohlcv
from ta.momentum import RSIIndicator

def get_indicators(symbol):
    """
    V77.3 第一版
    目前提供框架，之後會接 RSI、MACD、EMA 等真實計算。
    """
    price = get_price(symbol)
    rsi = None

    try:
        df = get_ohlcv(symbol, "1h", 100)
        rsi = round(float(RSIIndicator(df["close"], window=14).rsi().iloc[-1]), 2)
    except Exception:
        rsi = None

    return {
        "symbol": symbol,
        "price": price,
        "rsi": rsi,
        "macd": None,
        "ema20": None,
        "ema60": None,
        "trend": "UNKNOWN",
        "signal": "HOLD"
    }
