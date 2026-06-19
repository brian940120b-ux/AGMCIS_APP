from paper_trading import load_trades, load_account


def get_portfolio_summary():
    trades = load_trades()
    account = load_account()

    open_trades = [
        trade for trade in trades
        if trade["status"] == "OPEN"
    ]

    balance = account.get("balance", 10000)

    total_exposure = sum(
        float(trade.get("size_usdt", 0))
        for trade in open_trades
    )

    exposure_ratio = (
        total_exposure / balance * 100
        if balance > 0
        else 0
    )

    allocation_map = {}

    for trade in open_trades:
        symbol = trade["symbol"]
        size = float(trade.get("size_usdt", 0))

        if symbol not in allocation_map:
            allocation_map[symbol] = 0

        allocation_map[symbol] += size

    allocation = []

    for symbol, size in allocation_map.items():
        percent = (
            size / total_exposure * 100
            if total_exposure > 0
            else 0
        )

        allocation.append({
            "symbol": symbol,
            "size_usdt": round(size, 2),
            "percent": round(percent, 2)
        })

    allocation.sort(
        key=lambda x: x["size_usdt"],
        reverse=True
    )

    if exposure_ratio >= 80:
        risk_level = "高風險"
    elif exposure_ratio >= 40:
        risk_level = "中風險"
    elif exposure_ratio > 0:
        risk_level = "低風險"
    else:
        risk_level = "無持倉"

    return {
        "balance": round(balance, 2),
        "open_positions": len(open_trades),
        "total_exposure": round(total_exposure, 2),
        "exposure_ratio": round(exposure_ratio, 2),
        "risk_level": risk_level,
        "allocation": allocation,
        "open_trades": open_trades
    }