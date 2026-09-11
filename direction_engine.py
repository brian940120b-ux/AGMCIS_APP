def get_trade_direction(indicators):
    """
    只有在資料完整且多項條件同向時才給出方向,其餘一律 WAIT。
    indicators["data_ok"] 為 False 代表指標算失敗 —— 資料壞掉絕不可以被當成中性訊號。
    """
    if indicators.get("data_ok") is False:
        return "WAIT"

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
