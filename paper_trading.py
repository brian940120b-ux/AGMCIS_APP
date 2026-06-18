import json
import os
from datetime import datetime

ACCOUNT_FILE = "data/paper_account.json"
TRADES_FILE = "data/paper_trades.json"


def ensure_files():
    os.makedirs("data", exist_ok=True)

    if not os.path.exists(ACCOUNT_FILE):
        with open(ACCOUNT_FILE, "w", encoding="utf-8") as file:
            json.dump(
                {
                    "balance": 10000,
                    "wins": 0,
                    "losses": 0,
                    "trades": 0
                },
                file,
                ensure_ascii=False,
                indent=4
            )

    if not os.path.exists(TRADES_FILE):
        with open(TRADES_FILE, "w", encoding="utf-8") as file:
            json.dump([], file, ensure_ascii=False, indent=4)


def load_account():
    ensure_files()

    with open(ACCOUNT_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def save_account(account):
    ensure_files()

    with open(ACCOUNT_FILE, "w", encoding="utf-8") as file:
        json.dump(account, file, ensure_ascii=False, indent=4)


def load_trades():
    ensure_files()

    with open(TRADES_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def save_trades(trades):
    ensure_files()

    with open(TRADES_FILE, "w", encoding="utf-8") as file:
        json.dump(trades, file, ensure_ascii=False, indent=4)


def get_open_trade(symbol):
    trades = load_trades()

    for trade in trades:
        if trade["symbol"] == symbol and trade["status"] == "OPEN":
            return trade

    return None


def has_open_trade(symbol):
    return get_open_trade(symbol) is not None


def get_all_open_trades():
    trades = load_trades()

    return [
        trade for trade in trades
        if trade["status"] == "OPEN"
    ]


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

    trades = load_trades()

    trade = {
        "symbol": symbol,
        "signal": signal,
        "entry_price": float(entry_price),
        "size_usdt": float(size_usdt),
        "stoploss": float(stoploss) if stoploss != "-" and stoploss is not None else None,
        "takeprofit": float(takeprofit) if takeprofit != "-" and takeprofit is not None else None,
        "status": "OPEN",
        "opened_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    trades.append(trade)
    save_trades(trades)

    return {
        "success": True,
        "message": "模擬開倉成功",
        "trade": trade
    }


def close_paper_trade(symbol, exit_price, close_reason="手動平倉"):
    trades = load_trades()
    account = load_account()

    for trade in trades:
        if trade["symbol"] == symbol and trade["status"] == "OPEN":
            entry_price = float(trade["entry_price"])
            size_usdt = float(trade["size_usdt"])
            signal = trade["signal"]
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

            trade["status"] = "CLOSED"
            trade["exit_price"] = exit_price
            trade["pnl_pct"] = round(pnl_pct * 100, 2)
            trade["pnl_usdt"] = round(pnl_usdt, 2)
            trade["close_reason"] = close_reason
            trade["closed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            save_trades(trades)
            save_account(account)

            return {
                "success": True,
                "message": "模擬平倉成功",
                "trade": trade,
                "account": account
            }

    return {
        "success": False,
        "message": f"{symbol} 沒有可平倉的持倉"
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