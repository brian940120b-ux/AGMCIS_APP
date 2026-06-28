def calculate_mtf_score(timeframes):
    score = 0

    for tf in timeframes.values():
        trend = tf.get("trend")

        if trend == "BULLISH":
            score += 1
        elif trend == "BEARISH":
            score -= 1

    if score == 3:
        status = "STRONG_BULLISH"
    elif score >= 1:
        status = "BULLISH"
    elif score == 0:
        status = "NEUTRAL"
    else:
        status = "BEARISH"

    blocked_reason = None
    if score < 3:
        blocked_reason = "多時間框架未同向"

    return {
        "mtf_score": score,
        "mtf_status": status,
        "blocked_reason": blocked_reason
    }
