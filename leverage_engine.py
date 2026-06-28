def calculate_leverage(confidence, indicators=None, mtf_score=0):
    indicators = indicators or {}
    atr = indicators.get("atr")
    price = indicators.get("price")

    leverage = 1

    if confidence >= 95:
        leverage = 8
    elif confidence >= 90:
        leverage = 6
    elif confidence >= 80:
        leverage = 5
    elif confidence >= 70:
        leverage = 3
    else:
        leverage = 1

    if mtf_score < 3:
        leverage = min(leverage, 3)

    if atr and price:
        atr_ratio = atr / price
        if atr_ratio >= 0.05:
            leverage = min(leverage, 1)
        elif atr_ratio >= 0.03:
            leverage = min(leverage, 2)

    return leverage
