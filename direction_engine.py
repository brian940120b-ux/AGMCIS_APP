def get_trade_direction(indicators):
    rsi = indicators.get("rsi")
    macd = indicators.get("macd")
    macd_signal = indicators.get("macd_signal")
    ema20 = indicators.get("ema20")
    ema60 = indicators.get("ema60")

    if None in [rsi, macd, macd_signal, ema20, ema60]:
        return "WAIT"

    if ema20 > ema60 and macd > macd_signal and rsi >= 55:
        return "LONG"

    if ema20 < ema60 and macd < macd_signal and rsi <= 45:
        return "SHORT"

    return "WAIT"
