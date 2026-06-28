from market_universe import SCAN_SYMBOLS
from technical_service import get_indicators
from decision_engine import get_trade_signal
from ranking_engine import rank_decisions
from multi_timeframe_service import analyze_timeframes
from mtf_engine import calculate_mtf_score

def clamp(value, low=0, high=100):
    return max(low, min(high, value))

def calculate_scanner_confidence(indicators):
    trend = indicators.get("trend")
    rsi = indicators.get("rsi")
    macd_hist = indicators.get("macd_hist")

    score = 50

    if trend == "BULLISH":
        score += 25
    elif trend == "BEARISH":
        score -= 25

    if macd_hist is not None:
        if macd_hist > 0:
            score += 20
        elif macd_hist < 0:
            score -= 15

    if rsi is not None:
        if 45 <= rsi <= 60:
            score += 15
        elif 60 < rsi <= 70:
            score += 5
        elif rsi < 35:
            score += 10
        elif rsi > 70:
            score -= 20

    return round(clamp(score), 2)

def scan_market():
    results = []

    for symbol in SCAN_SYMBOLS:
        indicators = get_indicators(symbol)
        mtf = analyze_timeframes(symbol)
        trend = indicators.get("trend")

        confidence = calculate_scanner_confidence(indicators)

        mtf_result = calculate_mtf_score(mtf)
        mtf_score = mtf_result["mtf_score"]
        mtf_status = mtf_result["mtf_status"]
        action = "LONG" if confidence >= 65 and trend == "BULLISH" else "WATCH"
        trade_signal = get_trade_signal(confidence, action, indicators)

        if trade_signal == "🟢 Strong Buy" and mtf_score < 3:
            trade_signal = "🟢 Buy" if mtf_score >= 2 else "🟡 Hold"

        results.append({
            "symbol": symbol,
            "action": action,
            "trade_signal": trade_signal,
            "confidence": confidence,
            "mtf_score": mtf_score,
            "mtf_status": mtf_status,
            "entry_price": indicators.get("price"),
            "stoploss": round(indicators.get("price") - indicators.get("atr") * 2, 6) if indicators.get("price") and indicators.get("atr") else None,
            "takeprofit": round(indicators.get("price") + indicators.get("atr") * 3, 6) if indicators.get("price") and indicators.get("atr") else None,
            "blocked_reason": "多時間框架未同向" if mtf_score < 3 else ("MACD 動能轉弱" if indicators.get("macd_hist") is not None and indicators.get("macd_hist") < 0 else None),
            "takeprofit": round(indicators.get("price") + indicators.get("atr") * 3, 6) if indicators.get("price") and indicators.get("atr") else None,
            "blocked_reason": "多時間框架未同向" if mtf_score < 3 else ("MACD 動能轉弱" if indicators.get("macd_hist") is not None and indicators.get("macd_hist") < 0 else None),
            "indicators": indicators,
            "timeframes": mtf
        })

    return rank_decisions(results)

