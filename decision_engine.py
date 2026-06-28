def get_trade_signal(confidence, action, indicators=None, roi=0, distance_to_sl=None):
    indicators = indicators or {}

    rsi = indicators.get("rsi")
    trend = indicators.get("trend")
    macd_hist = indicators.get("macd_hist")
    atr = indicators.get("atr")
    price = indicators.get("price")

    if distance_to_sl is not None and distance_to_sl <= 1:
        return "🔴 Stoploss Warning"

    if atr is not None and price:
        atr_ratio = atr / price
        if atr_ratio >= 0.05:
            return "🟠 Reduce Position"
        elif atr_ratio >= 0.03 and confidence >= 65:
            return "🟡 Hold"

    if rsi is not None and rsi >= 75:
        return "🟠 Reduce Position"

    if confidence >= 80 and action == "LONG" and trend == "BULLISH" and (macd_hist is None or macd_hist >= 0):
        return "🟢 Strong Buy"

    if confidence >= 65 and action == "LONG":
        if macd_hist is not None and macd_hist < 0:
            return "🟡 Hold"
        return "🟢 Buy"

    if confidence <= 35:
        return "🔴 Strong Sell"

    if confidence <= 50:
        return "🔴 Sell"

    return "🟡 Hold"
