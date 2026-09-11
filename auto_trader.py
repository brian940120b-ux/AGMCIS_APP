"""
自動交易。

Phase 0.5 的關鍵修正:這條路徑原本完全沒有呼叫風控。
它被 scheduler 每 60 秒觸發、也被 HTTP 端點觸發,等於風控可以被整條繞過。
現在 run_auto_trader() 的第一件事就是問 Risk Engine,被擋下就直接結束。

其他修正:
  - 槓桿一律經過 risk_control.cap_leverage(),策略不能自行突破上限。
  - 停損停利由 scanner 依方向算好,這裡只驗證不再重算,避免兩處邏輯漂移。
  - 資料品質不合格(data_ok=False)的標的直接略過。
"""
from database_service import get_open_trade, get_open_trades
from direction import LONG, SHORT
from leverage_engine import calculate_leverage
from logger_service import logger
from notifier import notify_open_trade
from paper_trading import create_paper_trade
from risk_control import assert_can_open, cap_leverage
from scanner_service import scan_market

MAX_AUTO_POSITIONS = 3

LONG_SIGNALS = {"🟢 Buy", "🟢 Strong Buy"}
SHORT_SIGNALS = {"🔴 Sell", "🔴 Strong Sell"}


def run_auto_trader(position_size_usdt=1000):
    # ---------- HARD GATE:任何開倉之前都必須先過風控 ----------
    allowed, reason, risk_status = assert_can_open()
    if not allowed:
        return {
            "status": "BLOCKED_BY_RISK",
            "reason": reason,
            "system_status": risk_status["system_status"],
        }

    open_trades = get_open_trades(source="AUTO")
    if len(open_trades) >= MAX_AUTO_POSITIONS:
        logger.info("Auto Trader | BLOCKED_MAX_POSITIONS | %d 筆", len(open_trades))
        return {"status": "BLOCKED_MAX_POSITIONS", "open_positions": len(open_trades)}

    data = scan_market()
    top = data[:3]

    for candidate in data:
        symbol = candidate.get("symbol")

        if candidate.get("data_ok") is False:
            continue

        signal = candidate.get("trade_signal")
        if signal not in LONG_SIGNALS and signal not in SHORT_SIGNALS:
            continue

        if get_open_trade(symbol):
            continue

        entry = candidate.get("entry_price")
        stoploss = candidate.get("stoploss")
        takeprofit = candidate.get("takeprofit")

        if not entry or stoploss is None or takeprofit is None:
            logger.info("Auto Trader | SKIP_INCOMPLETE | %s", symbol)
            continue

        order_signal = SHORT if signal in SHORT_SIGNALS else LONG

        leverage = cap_leverage(calculate_leverage(
            candidate.get("confidence") or 0,
            candidate.get("indicators", {}),
            candidate.get("mtf_score", 0),
        ))

        result = create_paper_trade(
            symbol=symbol,
            entry_price=entry,
            signal=order_signal,
            size_usdt=position_size_usdt,
            stoploss=stoploss,
            takeprofit=takeprofit,
            leverage=leverage,
            source="AUTO",
        )

        logger.info(
            "Auto Trader | OPEN_ATTEMPT | %s | %s | lev=%sx | %s",
            symbol, order_signal, leverage, result.get("message"),
        )

        if result.get("success"):
            notify_open_trade(
                symbol, order_signal, entry, stoploss, takeprofit,
                leverage=leverage,
                confidence=candidate.get("confidence"),
                mtf_status=candidate.get("mtf_status"),
            )
            return {"status": "OPENED", "candidate": candidate, "result": result}

        # 開倉被拒(停損無效、已有持倉等)不算致命,換下一個候選。
        continue

    logger.info("Auto Trader | NO_TRADE_SIGNAL")
    return {"status": "NO_TRADE_SIGNAL", "top_candidates": top}
