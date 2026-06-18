from paper_trading import load_trades, load_account


START_BALANCE = 10000


def build_equity_curve(closed_trades):
    equity = START_BALANCE
    curve = [equity]

    for trade in closed_trades:
        pnl = trade.get("pnl_usdt", 0)
        equity += pnl
        curve.append(round(equity, 2))

    return curve


def calculate_max_drawdown(equity_curve):
    if not equity_curve:
        return 0

    peak = equity_curve[0]
    max_drawdown = 0

    for value in equity_curve:
        if value > peak:
            peak = value

        drawdown = (peak - value) / peak * 100

        if drawdown > max_drawdown:
            max_drawdown = drawdown

    return round(max_drawdown, 2)


def calculate_profit_factor(closed_trades):
    gross_profit = sum(
        trade.get("pnl_usdt", 0)
        for trade in closed_trades
        if trade.get("pnl_usdt", 0) > 0
    )

    gross_loss = abs(
        sum(
            trade.get("pnl_usdt", 0)
            for trade in closed_trades
            if trade.get("pnl_usdt", 0) < 0
        )
    )

    if gross_loss == 0:
        return 0 if gross_profit == 0 else 999

    return round(gross_profit / gross_loss, 2)


def get_trade_analytics():
    trades = load_trades()
    account = load_account()

    closed_trades = [
        trade for trade in trades
        if trade["status"] == "CLOSED"
    ]

    total_trades = len(closed_trades)

    if total_trades == 0:
        return {
            "total_trades": 0,
            "total_pnl": 0,
            "total_return_pct": 0,
            "win_rate": 0,
            "avg_pnl": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "risk_reward_ratio": 0,
            "profit_factor": 0,
            "max_drawdown": 0,
            "best_symbol": "-",
            "worst_symbol": "-",
            "symbol_stats": [],
            "recent_trades": [],
            "equity_curve": [START_BALANCE],
            "current_balance": account.get("balance", START_BALANCE)
        }

    total_pnl = sum(
        trade.get("pnl_usdt", 0)
        for trade in closed_trades
    )

    total_return_pct = total_pnl / START_BALANCE * 100

    wins_list = [
        trade.get("pnl_usdt", 0)
        for trade in closed_trades
        if trade.get("pnl_usdt", 0) > 0
    ]

    losses_list = [
        trade.get("pnl_usdt", 0)
        for trade in closed_trades
        if trade.get("pnl_usdt", 0) < 0
    ]

    wins = len(wins_list)
    losses = len(losses_list)

    win_rate = wins / total_trades * 100
    avg_pnl = total_pnl / total_trades

    avg_win = (
        sum(wins_list) / len(wins_list)
        if wins_list
        else 0
    )

    avg_loss = (
        sum(losses_list) / len(losses_list)
        if losses_list
        else 0
    )

    risk_reward_ratio = (
        abs(avg_win / avg_loss)
        if avg_loss != 0
        else 0
    )

    equity_curve = build_equity_curve(closed_trades)
    max_drawdown = calculate_max_drawdown(equity_curve)
    profit_factor = calculate_profit_factor(closed_trades)

    symbol_map = {}

    for trade in closed_trades:
        symbol = trade["symbol"]
        pnl = trade.get("pnl_usdt", 0)

        if symbol not in symbol_map:
            symbol_map[symbol] = {
                "symbol": symbol,
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "pnl": 0
            }

        symbol_map[symbol]["trades"] += 1
        symbol_map[symbol]["pnl"] += pnl

        if pnl > 0:
            symbol_map[symbol]["wins"] += 1
        else:
            symbol_map[symbol]["losses"] += 1

    symbol_stats = list(symbol_map.values())

    for item in symbol_stats:
        item["win_rate"] = round(
            item["wins"] / item["trades"] * 100,
            2
        )
        item["pnl"] = round(item["pnl"], 2)

    symbol_stats.sort(
        key=lambda x: x["pnl"],
        reverse=True
    )

    best_symbol = symbol_stats[0]["symbol"]
    worst_symbol = symbol_stats[-1]["symbol"]

    recent_trades = closed_trades[-10:]
    recent_trades.reverse()

    return {
        "total_trades": total_trades,
        "total_pnl": round(total_pnl, 2),
        "total_return_pct": round(total_return_pct, 2),
        "win_rate": round(win_rate, 2),
        "avg_pnl": round(avg_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "risk_reward_ratio": round(risk_reward_ratio, 2),
        "profit_factor": profit_factor,
        "max_drawdown": max_drawdown,
        "best_symbol": best_symbol,
        "worst_symbol": worst_symbol,
        "symbol_stats": symbol_stats,
        "recent_trades": recent_trades,
        "equity_curve": equity_curve,
        "current_balance": round(account.get("balance", START_BALANCE), 2)
    }