from market_universe import SCAN_SYMBOLS
from technical_service import get_indicators
from decision_engine import get_trade_signal
from ranking_engine import rank_decisions

def scan_market():
    results = []

    for symbol in SCAN_SYMBOLS:
        indicators = get_indicators(symbol)

        trend = indicators.get("trend")
        rsi = indicators.get("rsi")
        macd_hist = indicators.get("macd_hist")

        confidence = 50

        if trend == "BULLISH":
            confidence += 15
        if macd_hist is not None and macd_hist > 0:
            confidence += 10
        if rsi is not None and 45 <= rsi <= 65:
            confidence += 10
        if rsi is not None and rsi >= 75:
            confidence -= 15

        confidence = max(0, min(100, round(confidence, 2)))
        action = "LONG" if trend == "BULLISH" else "WATCH"
        trade_signal = get_trade_signal(confidence, action, indicators)

        results.append({
            "symbol": symbol,
            "action": action,
            "trade_signal": trade_signal,
            "confidence": confidence,
            "indicators": indicators
        })

    return rank_decisions(results)
