from database_service import (
    get_account,
    update_account,
    get_trades,
    get_open_trades,
    get_open_trade,
    insert_trade,
    close_trade
)


def load_account():
    return get_account()


def save_account(account):
    update_account(
        account["balance"],
        account["wins"],
        account["losses"],
        account["trades"]
    )


def load_trades():
    return get_trades()


def save_trades(trades):
    # V13.4 起不再使用 JSON 儲存
    pass


def get_all_open_trades():
    return get_open_trades()


def has_open_trade(symbol):
    return get_open_trade(symbol) is not None


def create_paper_trade(
    symbol,
    entry_price,
    signal,
    size_usdt=1000,
    stoploss=None,
    takeprofit=None
):
    if has_open_trade(symbol):
        return {
            "success": False,
            "message": f"{symbol} 已有持倉，不重複開倉"
        }

    insert_trade(
        symbol=symbol,
        signal=signal,
        entry_price=float(entry_price),
        size_usdt=float(size_usdt),
        stoploss=None if stoploss == "-" else stoploss,
        takeprofit=None if takeprofit == "-" else takeprofit
    )

    return {
        "success": True,
        "message": "模擬開倉成功",
        "trade": {
            "symbol": symbol,
            "signal": signal,
            "entry_price": float(entry_price),
            "size_usdt": float(size_usdt),
            "stoploss": None if stoploss == "-" else stoploss,
            "takeprofit": None if takeprofit == "-" else takeprofit,
            "status": "OPEN"
        }
    }


def close_paper_trade(symbol, exit_price, close_reason="手動平倉"):
    trades = get_all_open_trades()
    account = load_account()

    target_trade = None

    for trade in trades:
        if trade["symbol"] == symbol:
            target_trade = trade
            break

    if target_trade is None:
        return {
            "success": False,
            "message": f"{symbol} 沒有可平倉的持倉"
        }

    entry_price = float(target_trade["entry_price"])
    size_usdt = float(target_trade["size_usdt"])
    signal = target_trade["signal"]
    exit_price = float(exit_price)

    if signal == "做多":
        pnl_pct = (exit_price - entry_price) / entry_price
    elif signal == "做空":
        pnl_pct = (entry_price - exit_price) / entry_price
    else:
        pnl_pct = 0

    pnl_usdt = size_usdt * pnl_pct

    account["balance"] += pnl_usdt
    account["trades"] += 1

    if pnl_usdt > 0:
        account["wins"] += 1
    else:
        account["losses"] += 1

    update_account(
        account["balance"],
        account["wins"],
        account["losses"],
        account["trades"]
    )

    close_trade(
        symbol=symbol,
        exit_price=exit_price,
        pnl_pct=round(pnl_pct * 100, 2),
        pnl_usdt=round(pnl_usdt, 2),
        close_reason=close_reason
    )

    closed_trade = target_trade.copy()
    closed_trade["status"] = "CLOSED"
    closed_trade["exit_price"] = exit_price
    closed_trade["pnl_pct"] = round(pnl_pct * 100, 2)
    closed_trade["pnl_usdt"] = round(pnl_usdt, 2)
    closed_trade["close_reason"] = close_reason

    return {
        "success": True,
        "message": "模擬平倉成功",
        "trade": closed_trade,
        "account": account
    }


def get_paper_summary():
    account = load_account()
    trades = load_trades()

    open_trades = [
        trade for trade in trades
        if trade["status"] == "OPEN"
    ]

    closed_trades = [
        trade for trade in trades
        if trade["status"] == "CLOSED"
    ]

    total_trades = account["trades"]
    wins = account["wins"]

    win_rate = (
        wins / total_trades * 100
        if total_trades > 0
        else 0
    )

    return {
        "balance": round(account["balance"], 2),
        "wins": account["wins"],
        "losses": account["losses"],
        "trades": total_trades,
        "win_rate": round(win_rate, 2),
        "open_trades": open_trades,
        "closed_trades": closed_trades
    }