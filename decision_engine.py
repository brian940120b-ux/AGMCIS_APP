def get_trade_signal(confidence, action):
    if confidence >= 80 and action == "LONG":
        return "🟢 Strong Buy"
    if confidence >= 65 and action == "LONG":
        return "🟢 Buy"
    if confidence <= 35:
        return "🔴 Strong Sell"
    if confidence <= 50:
        return "🔴 Sell"
    return "🟡 Hold"
