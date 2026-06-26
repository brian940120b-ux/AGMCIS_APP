from market_data import get_price

def get_indicators(symbol):
    """
    V77.3 第一版
    目前提供框架，之後會接 RSI、MACD、EMA 等真實計算。
    """
    price = get_price(symbol)

    return {
        "symbol": symbol,
        "price": price,
        "rsi": None,
        "macd": None,
        "ema20": None,
        "ema60": None,
        "trend": "UNKNOWN",
        "signal": "HOLD"
    }
