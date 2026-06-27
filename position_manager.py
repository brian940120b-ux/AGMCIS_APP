from paper_trading import get_all_open_trades, close_paper_trade
from market_data import get_price
from logger_service import logger
from notifier import send_telegram


def manage_open_positions():
    open_trades = get_all_open_trades()

    if not open_trades:
        logger.info("目前沒有持倉需要管理")
        return

    closed_count = 0

    for trade in open_trades:
        symbol = trade["symbol"]
        signal = trade["signal"]
        stoploss = trade.get("stoploss")
        takeprofit = trade.get("takeprofit")

        if stoploss is None or takeprofit is None:
            logger.warning(f"{symbol} 缺少 TP/SL，略過")
            continue

        current_price = get_price(symbol)

        if current_price is None:
            logger.warning(f"{symbol} 無法取得目前價格")
            continue

        should_close = False
        close_reason = ""

        if signal == "做多":
            if current_price <= stoploss:
                should_close = True
                close_reason = "自動停損"
            elif current_price >= takeprofit:
                should_close = True
                close_reason = "自動止盈"

        elif signal == "做空":
            if current_price >= stoploss:
                should_close = True
                close_reason = "自動停損"
            elif current_price <= takeprofit:
                should_close = True
                close_reason = "自動止盈"

        if should_close:
            result = close_paper_trade(
                symbol=symbol,
                exit_price=current_price,
                close_reason=close_reason
            )

            if result["success"]:
                closed_count += 1
                closed_trade = result["trade"]

                send_telegram(
                    f"""
📕 AGMCIS 自動模擬平倉

幣種：{closed_trade["symbol"]}
方向：{closed_trade["signal"]}
原因：{close_reason}

進場價：{closed_trade["entry_price"]}
出場價：{closed_trade["exit_price"]}

盈虧：{closed_trade["pnl_usdt"]} USDT
報酬率：{closed_trade["pnl_pct"]}%

模式：Paper Trading
"""
                )

    logger.info(f"自動平倉檢查完成，平倉數：{closed_count}")
