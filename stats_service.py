from database_service import get_trades, get_account, get_open_trades

def get_stats():
    trades = get_trades()
    account = get_account()

    closed = [t for t in trades if t["status"] == "CLOSED"]

    wins = [t for t in closed if float(t.get("pnl_usdt") or 0) > 0]
    losses = [t for t in closed if float(t.get("pnl_usdt") or 0) <= 0]

    avg_win = (
        sum(float(t["pnl_usdt"]) for t in wins) / len(wins)
        if wins else 0
    )

    avg_loss = (
        sum(float(t["pnl_usdt"]) for t in losses) / len(losses)
        if losses else 0
    )

    gross_profit = sum(float(t["pnl_usdt"]) for t in wins)
    gross_loss = abs(sum(float(t["pnl_usdt"]) for t in losses))

    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0 else 999
    )

    return {
        "total": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins)/len(closed)*100,2) if closed else 0,
        "avg_win": round(avg_win,2),
        "avg_loss": round(avg_loss,2),
        "profit_factor": round(profit_factor,2),
        "balance": account["balance"],
        "open_positions": len(get_open_trades())
    }
