from fastapi import APIRouter

router = APIRouter()
import time
from database_service import get_account, get_open_trades, get_closed_trades
from market_data import get_price
from risk_control import get_risk_control_status


START_TIME = time.time()


@router.get("/api/dashboard")
def api_dashboard():
    a = get_account()
    o = get_open_trades()
    c = get_closed_trades()
    risk = get_risk_control_status()

    positions = []

    for t in o:
        symbol = t.get("symbol")
        signal = t.get("signal")

        entry = float(t.get("entry_price") or 0)
        size = float(t.get("size_usdt") or 0)
        leverage = float(t.get("leverage") or 3)
        current = get_price(symbol)

        if current and entry:
            if signal == "做多":
                raw = (float(current) - entry) / entry * 100
            else:
                raw = (entry - float(current)) / entry * 100

            roi = round(raw * leverage, 2)
            upnl = round(size * roi / 100, 2)
        else:
            roi = 0
            upnl = 0

        positions.append({
            "symbol": symbol,
            "signal": signal,
            "leverage": leverage,
            "entry": entry,
            "current": current,
            "roi": roi,
            "upnl": upnl,
            "stoploss": t.get("stoploss"),
            "takeprofit": t.get("takeprofit"),
            "distance_to_sl": round(abs((float(current)-float(t.get("stoploss")))/float(current)*100),2) if current and t.get("stoploss") else 0,
            "distance_to_tp": round(abs((float(t.get("takeprofit"))-float(current))/float(current)*100),2) if current and t.get("takeprofit") else 0,
            "opened_at": t.get("opened_at"),
            "trailing_enabled": roi >= 5,
            "trailing_gap": 2 if roi >= 20 else 3 if roi >= 10 else 4 if roi >= 5 else 0
        })

    total_upnl = round(sum(p["upnl"] for p in positions), 2)

    return {
        "balance": a.get("balance"),
        "trades": a.get("trades"),
        "wins": a.get("wins"),
        "losses": a.get("losses"),
        "open_count": len(o),
        "closed_count": len(c),
        "total_open_upnl": total_upnl,
        "risk_level": "HIGH" if total_upnl < -100 else "MEDIUM" if total_upnl < 0 else "LOW",
        "system_status": risk.get("system_status"),
        "uptime_seconds": int(time.time() - START_TIME),
        "positions": positions
    }
