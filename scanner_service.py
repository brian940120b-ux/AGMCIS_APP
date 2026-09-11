from market_universe import SCAN_SYMBOLS
from technical_service import get_indicators
from decision_engine import get_trade_signal
from ranking_engine import rank_decisions
from multi_timeframe_service import analyze_timeframes
from mtf_engine import calculate_mtf_score
from direction_engine import get_trade_direction

def clamp(value, low=0, high=100):
    return max(low, min(high, value))

def calculate_scanner_confidence(indicators):
    # 資料壞掉時回傳 None,而不是 50 分的「中性」—— 讓下游可以區分中性與無資料。
    if indicators.get("data_ok") is False:
        return None

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
        price = indicators.get("price")
        atr = indicators.get("atr")

        # 資料品質 gate:指標算不出來就不產生任何交易訊號。
        if indicators.get("data_ok") is False:
            results.append({
                "symbol": symbol,
                "action": "WAIT",
                "trade_signal": "⚪ No Data",
                "confidence": None,
                "mtf_score": 0,
                "mtf_status": "UNKNOWN",
                "entry_price": price,
                "stoploss": None,
                "takeprofit": None,
                "blocked_reason": f"資料異常:{indicators.get('data_error')}",
                "data_ok": False,
                "indicators": indicators,
                "timeframes": {},
            })
            continue

        mtf = analyze_timeframes(symbol)
        confidence = calculate_scanner_confidence(indicators)

        mtf_result = calculate_mtf_score(mtf)
        mtf_score = mtf_result["mtf_score"]
        mtf_status = mtf_result["mtf_status"]
        mtf_blocked_reason = mtf_result.get("blocked_reason")

        action = get_trade_direction(indicators)
        trade_signal = get_trade_signal(confidence, action, indicators)

        if trade_signal == "🟢 Strong Buy" and mtf_score < 3:
            trade_signal = "🟢 Buy" if mtf_score >= 2 else "🟡 Hold"

        if trade_signal in ["🔴 Sell", "🔴 Strong Sell"]:
            action = "SHORT"

        # 停損停利依方向計算。原本只算做多方向(price - atr),
        # 做空時會把停損放在進場價下方 —— 方向錯的停損會立刻觸發。
        stoploss = takeprofit = None
        if price and atr:
            if action == "SHORT":
                stoploss = round(price + atr * 2, 6)
                takeprofit = round(price - atr * 3, 6)
            else:
                stoploss = round(price - atr * 2, 6)
                takeprofit = round(price + atr * 3, 6)

        macd_hist = indicators.get("macd_hist")
        blocked_reason = mtf_blocked_reason or (
            "MACD 動能轉弱" if macd_hist is not None and macd_hist < 0 else None
        )

        results.append({
            "symbol": symbol,
            "action": action,
            "trade_signal": trade_signal,
            "confidence": confidence,
            "mtf_score": mtf_score,
            "mtf_status": mtf_status,
            "entry_price": price,
            "stoploss": stoploss,
            "takeprofit": takeprofit,
            "blocked_reason": blocked_reason,
            "data_ok": True,
            "indicators": indicators,
            "timeframes": mtf,
        })

    return rank_decisions(results)
