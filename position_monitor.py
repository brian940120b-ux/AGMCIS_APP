from database_service import get_open_trades
from market_data import get_price
from paper_trading import close_paper_trade
from logger_service import logger
from notifier import notify_close_trade

def run_position_monitor():
    open_trades = get_open_trades()

    checked_symbols = []
    closed = []

    for t in open_trades:
        symbol = t.get("symbol")
        price = get_price(symbol)
        reason = None
        signal = t.get("signal")
        stoploss = t.get("stoploss")
        takeprofit = t.get("takeprofit")

        if signal == "做多":
            if price <= stoploss:
                reason = "自動止損"
            elif price >= takeprofit:
                reason = "自動止盈"

        elif signal == "做空":
            if price >= stoploss:
                reason = "自動止損"
            elif price <= takeprofit:
                reason = "自動止盈"
        if reason:
            result = close_paper_trade(symbol, price, reason)
            if result.get("success"):
                trade = result.get("trade", {})
                notify_close_trade(
                    symbol,
                    signal,
                    price,
                    pnl_pct=trade.get("pnl_pct"),
                    pnl_usdt=trade.get("pnl_usdt"),
                    reason=reason
                )

            logger.info(f"Position Monitor | CLOSE | {symbol} | {signal} | {reason} | price={price}")
            closed.append(result)

        checked_symbols.append({
            "symbol": symbol,
            "price": price
        })

    return {
        "checked": len(open_trades),
        "closed_count": 0,
        "checked_symbols": checked_symbols,
        "closed": []
    }
