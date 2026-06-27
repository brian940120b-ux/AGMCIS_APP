def get_trade_signal(confidence, action, indicators=None, roi=0, distance_to_sl=None):
    indicators = indicators or {}

    rsi = indicators.get("rsi")
    trend = indicators.get("trend")
    macd_hist = indicators.get("macd_hist")

    if distance_to_sl is not None and distance_to_sl <= 1:
        return "🔴 Stoploss Warning"

    if rsi is not None and rsi >= 75:
        return "🟠 Reduce Position"

    if confidence >= 80 and action == "LONG" and trend == "BULLISH" and (macd_hist is None or macd_hist >= 0):
        return "🟢 Strong Buy"

    if confidence >= 65 and action == "LONG":
        return "🟢 Buy"

    if confidence <= 35:
        return "🔴 Strong Sell"

    if confidence <= 50:
        return "🔴 Sell"

    return "🟡 Hold"
