from database_service import get_open_trades

def get_ai_decisions():
    positions = get_open_trades()
    results = []

    for p in positions:
        symbol = p.get("symbol")
        signal = p.get("signal", "觀察")

        score = 70

        if signal == "做多":
            action = "LONG"
        elif signal == "做空":
            action = "SHORT"
        else:
            action = "WATCH"

        results.append({
            "symbol": symbol,
            "action": action,
            "confidence": score,
            "trend_score": score,
            "risk_score": 100 - score,
            "momentum_score": score,
            "reason": "依目前持倉方向與系統狀態產生初步 AI 評分"
        })

    return results
