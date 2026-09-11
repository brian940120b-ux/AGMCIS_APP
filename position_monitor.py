"""
持倉監控:檢查 TP / SL 並平倉。

Phase 0.5 的修正:
  1. 回傳真實的平倉數。原本 closed_count 與 closed 是寫死的 0 與 [],
     即使實際平了倉,scheduler log 與 /api/scheduler_status 也永遠顯示 0。
  2. 取價或停損為 None 時不再直接比較大小(原本會拋 TypeError,
     被 scheduler 的 except 吞掉,導致該輪所有持倉都不檢查停損)。
  3. 缺停損的倉位改為 WARNING 級別的 unprotected 清單回報,不再靜默跳過。
     沒有停損的倉位等於沒有風險上限,必須讓維運看得到。

position_manager.manage_open_positions() 也改為呼叫這裡的同一份邏輯,
不再有兩套各自實作的平倉判斷。
"""
from database_service import get_open_trades
from direction import is_long, is_short
from logger_service import logger
from market_data import get_price
from notifier import notify_close_trade
from paper_trading import close_paper_trade


def _exit_reason(signal, price, stoploss, takeprofit):
    """回傳平倉原因,沒有觸發條件則回傳 None。stoploss / takeprofit 可為 None。"""
    if is_long(signal):
        if stoploss is not None and price <= stoploss:
            return "自動止損"
        if takeprofit is not None and price >= takeprofit:
            return "自動止盈"
        return None

    if is_short(signal):
        if stoploss is not None and price >= stoploss:
            return "自動止損"
        if takeprofit is not None and price <= takeprofit:
            return "自動止盈"
        return None

    return None


def run_position_monitor(notify=True):
    open_trades = get_open_trades()

    checked_symbols = []
    closed = []
    skipped = []
    unprotected = []

    for trade in open_trades:
        symbol = trade.get("symbol")
        signal = trade.get("signal")
        stoploss = trade.get("stoploss")
        takeprofit = trade.get("takeprofit")

        if not (is_long(signal) or is_short(signal)):
            skipped.append({"symbol": symbol, "reason": f"方向無法辨識 ({signal!r})"})
            logger.error("Position Monitor | UNKNOWN_DIRECTION | %s | %r", symbol, signal)
            continue

        if stoploss is None:
            # 不是可以跳過的小事:這個倉位沒有風險上限。
            unprotected.append({"symbol": symbol, "signal": signal})
            logger.warning(
                "Position Monitor | NO_STOPLOSS | %s | %s | 該倉位沒有停損保護",
                symbol, signal,
            )

        price = get_price(symbol)

        if price is None:
            skipped.append({"symbol": symbol, "reason": "無法取得目前價格"})
            logger.warning("Position Monitor | NO_PRICE | %s | 本輪跳過停損檢查", symbol)
            continue

        price = float(price)
        checked_symbols.append({"symbol": symbol, "price": price})

        reason = _exit_reason(signal, price, stoploss, takeprofit)

        if not reason:
            continue

        result = close_paper_trade(symbol, price, reason)

        if not result.get("success"):
            if result.get("already_closed"):
                logger.info("Position Monitor | ALREADY_CLOSED | %s", symbol)
            else:
                logger.error(
                    "Position Monitor | CLOSE_FAILED | %s | %s",
                    symbol, result.get("message"),
                )
            continue

        closed_trade = result["trade"]
        closed.append(closed_trade)

        logger.info(
            "Position Monitor | CLOSE | %s | %s | %s | price=%s roi=%s%% pnl=%s",
            symbol, signal, reason, price,
            closed_trade.get("pnl_pct"), closed_trade.get("pnl_usdt"),
        )

        if notify:
            notify_close_trade(
                symbol,
                signal,
                price,
                pnl_pct=closed_trade.get("pnl_pct"),
                pnl_usdt=closed_trade.get("pnl_usdt"),
                reason=reason,
            )

    return {
        "checked": len(open_trades),
        "closed_count": len(closed),
        "closed": closed,
        "checked_symbols": checked_symbols,
        "skipped": skipped,
        "unprotected": unprotected,
    }


if __name__ == "__main__":
    print(run_position_monitor())
