from database_service import get_account, get_open_trades

def get_portfolio():
    account = get_account()
    open_trades = get_open_trades()

    balance = float(account.get("balance", 0))
    margin = sum(float(t.get("size_usdt", 0)) for t in open_trades)
    available = balance - margin

    return {
        "balance": round(balance, 2),
        "open_positions": len(open_trades),
        "margin_used": round(margin, 2),
        "available": round(available, 2),
        "positions": open_trades,
    }
