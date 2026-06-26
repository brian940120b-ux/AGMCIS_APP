from database_service import get_open_trades
from market_data import get_price
from technical_service import get_indicators

def get_ai_decisions():
    positions = get_open_trades()
    results = []

    for p in positions:
        symbol = p.get("symbol")
        indicators = get_indicators(symbol)
        signal = p.get("signal", "觀察")

        entry=float(p.get("entry_price") or 0)
        current=float(get_price(symbol) or entry or 0)
        leverage=float(p.get("leverage") or 3)
        if entry:
            roi=((current-entry)/entry)*100
            if signal=="做空":
                roi=-roi
            score=max(0,min(100,round(70+roi*leverage)))
        else:
            score=70

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
            "reason": "依目前持倉方向與系統狀態產生初步 AI 評分",
            "indicators": indicators
        })

    return results
