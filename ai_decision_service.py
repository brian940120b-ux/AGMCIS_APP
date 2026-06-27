from database_service import get_open_trades
from market_data import get_price
from technical_service import get_indicators
from decision_engine import get_trade_signal
from logger_service import logger
from ranking_engine import rank_decisions

def clamp(v, low=0, high=100):
    return max(low, min(high, v))

def score_roi(roi):
    return clamp(50 + roi * 2)

def score_rsi(rsi):
    if rsi is None:
        return 50
    if rsi > 75:
        return 35
    if rsi > 70:
        return 45
    if 45 <= rsi <= 65:
        return 75
    if rsi < 30:
        return 40
    return 60



def score_atr(atr, price):
    if atr is None or not price:
        return 50

    atr_pct = (atr / price) * 100

    if atr_pct > 5:
        return 30
    if atr_pct > 3:
        return 45
    if atr_pct > 1.5:
        return 60
    return 75

def score_macd(macd_hist):
    if macd_hist is None:
        return 50
    if macd_hist > 0:
        return 80
    if macd_hist < 0:
        return 40
    return 50

def score_trend(trend):
    if trend == "BULLISH":
        return 80
    if trend == "BEARISH":
        return 40
    return 50

def score_risk(distance_sl, distance_tp):
    risk = 50

    if distance_sl is not None:
        if distance_sl < 1:
            risk -= 25
        elif distance_sl < 2:
            risk -= 15
        elif distance_sl < 4:
            risk += 5
        else:
            risk += 15

    if distance_tp is not None:
        if distance_tp < 1:
            risk -= 10
        elif distance_tp > 4:
            risk += 10

    return clamp(risk)

def get_ai_decisions():
    positions = get_open_trades()
    results = []

    for p in positions:
        symbol = p.get("symbol")
        signal = p.get("signal", "觀察")
        indicators = get_indicators(symbol)

        entry = float(p.get("entry_price") or 0)
        current = float(get_price(symbol) or entry or 0)
        leverage = float(p.get("leverage") or 3)

        stoploss = float(p.get("stoploss") or 0)
        takeprofit = float(p.get("takeprofit") or 0)

        if entry:
            roi = ((current - entry) / entry) * 100
            if signal == "做空":
                roi = -roi
            roi = round(roi * leverage, 2)
        else:
            roi = 0

        distance_sl = abs((current - stoploss) / current * 100) if current and stoploss else None
        distance_tp = abs((takeprofit - current) / current * 100) if current and takeprofit else None

        roi_score = score_roi(roi)
        rsi_score = score_rsi(indicators.get("rsi"))
        trend_score = score_trend(indicators.get("trend"))
        macd_score = score_macd(indicators.get("macd_hist"))
        atr_score = score_atr(indicators.get("atr"), current)
        risk_score = score_risk(distance_sl, distance_tp)

        confidence = round(
            roi_score * 0.20 +
            rsi_score * 0.18 +
            trend_score * 0.22 +
            macd_score * 0.15 +
            risk_score * 0.15 +
            atr_score * 0.10,
            2
        )

        if signal == "做多":
            action = "LONG"
        elif signal == "做空":
            action = "SHORT"
        else:
            action = "WATCH"

        if confidence >= 75:
            reason = "多因子 AI 評估：趨勢與風險條件較佳，可持續觀察。"
        elif confidence >= 55:
            reason = "多因子 AI 評估：條件中性，建議維持風控。"
        else:
            reason = "多因子 AI 評估：信心偏低，建議降低風險。"

        trade_signal = get_trade_signal(confidence, action, indicators, roi, distance_sl)

        logger.info(f"AI Decision | {symbol} | {trade_signal} | confidence={confidence:.2f} | roi={roi}")

        results.append({
            "symbol": symbol,
            "action": action,
            "trade_signal": trade_signal,
            "confidence": confidence,
            "trend_score": round(trend_score, 2),
            "risk_score": round(risk_score, 2),
            "momentum_score": round(roi_score, 2),
            "roi": roi,
            "distance_to_sl": round(distance_sl, 2) if distance_sl is not None else None,
            "distance_to_tp": round(distance_tp, 2) if distance_tp is not None else None,
            "reason": reason,
            "indicators": indicators
        })

    ranked = rank_decisions(results)

    top3 = ranked[:3]
    summary = " | ".join([
        f"{i+1}. {d.get('symbol')} {d.get('trade_signal')} {d.get('confidence')}%"
        for i, d in enumerate(top3)
    ])

    logger.info(f"Top Opportunities | {summary}")

    return ranked
