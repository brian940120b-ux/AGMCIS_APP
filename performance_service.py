from database_service import get_trades, get_account

def get_performance_summary():
    account = get_account()
    trades = get_trades()

    closed = [t for t in trades if t.get("status") == "CLOSED"]

    if not closed:
        return {
            "total_trades": len(closed),
            "closed_trades": 0,
            "win_rate": 0,
            "total_pnl": 0,
            "avg_pnl": 0,
            "best": None,
            "worst": None
        }

    total_pnl = sum(float(t.get("pnl_usdt") or 0) for t in closed)
    wins = [t for t in closed if float(t.get("pnl_usdt") or 0) > 0]
    win_rate = len(wins) / len(closed) * 100

    best = max(closed, key=lambda t: float(t.get("pnl_usdt") or 0))
    worst = min(closed, key=lambda t: float(t.get("pnl_usdt") or 0))

    return {
        "total_trades": len(closed),
        "closed_trades": len(closed),
        "win_rate": round(win_rate, 2),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / len(closed), 2),
        "best": best,
        "worst": worst
    }
