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

Phase 10 加入**強制平倉**。判定順序與回測引擎一致:
停損與強平之中,**離進場價較近的那個先觸發**,不是無條件先看強平。
做多停損 99、強平 91 時,價格是先經過 99 的,那筆是正常停損。

⚠️ 已知的樂觀偏誤:這裡比對的是輪詢當下的**單一價格**,不是這段期間的
high / low。兩次輪詢之間穿刺停損又彈回來的行情,這裡看不到 ——
實際交易所的觸發單會成交,模擬盤不會。這會讓模擬勝率偏高。
要修掉需要 WebSocket 逐筆價格(Phase 12 / 13)。
"""
from database_service import get_open_trades
from direction import is_long, is_short
from logger_service import logger
from market_data import get_price
from notifier import notify_close_trade
from paper_trading import close_paper_trade


LIQUIDATION_REASON = "強制平倉"


def _adverse_level(signal, stoploss, liquidation_price):
    """
    停損與強平之中,離進場價較近的那個會先被觸發。

    回傳 (觸發價, 是否為強平)。兩個都沒有就回 (None, False)。
    """
    if stoploss is None and liquidation_price is None:
        return None, False

    if stoploss is None:
        return liquidation_price, True

    if liquidation_price is None:
        return stoploss, False

    if is_long(signal):
        # 做多時價格往下走,先碰到的是**較高**的那個
        if liquidation_price > stoploss:
            return liquidation_price, True
        return stoploss, False

    # 做空時價格往上走,先碰到的是**較低**的那個
    if liquidation_price < stoploss:
        return liquidation_price, True
    return stoploss, False


def _exit_reason(signal, price, stoploss, takeprofit, liquidation_price=None):
    """
    回傳 (平倉原因, 是否為強平)。沒有觸發條件則回傳 (None, False)。
    stoploss / takeprofit / liquidation_price 都可為 None。
    """
    level, liquidated = _adverse_level(signal, stoploss, liquidation_price)

    if is_long(signal):
        if level is not None and price <= level:
            return (LIQUIDATION_REASON if liquidated else "自動止損"), liquidated
        if takeprofit is not None and price >= takeprofit:
            return "自動止盈", False
        return None, False

    if is_short(signal):
        if level is not None and price >= level:
            return (LIQUIDATION_REASON if liquidated else "自動止損"), liquidated
        if takeprofit is not None and price <= takeprofit:
            return "自動止盈", False
        return None, False

    return None, False


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
        liquidation_price = trade.get("liquidation_price")

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

        reason, liquidated = _exit_reason(
            signal, price, stoploss, takeprofit, liquidation_price,
        )

        if not reason:
            continue

        if liquidated:
            # 強平在強平價成交,不是在輪詢到的那個價格成交。
            logger.error(
                "Position Monitor | LIQUIDATION | %s | %s | 強平價=%s 當前價=%s",
                symbol, signal, liquidation_price, price,
            )
            result = close_paper_trade(
                symbol, liquidation_price, reason, liquidated=True,
            )
        else:
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
        "liquidated_count": len([c for c in closed if c.get("liquidated")]),
        "checked_symbols": checked_symbols,
        "skipped": skipped,
        "unprotected": unprotected,
    }


if __name__ == "__main__":
    print(run_position_monitor())
