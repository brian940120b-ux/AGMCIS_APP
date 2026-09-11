"""
自動交易。

Phase 5 之後的完整鏈路:

    scan_market()            訊號
        -> TradeIntent       Agent/策略唯一能產出的東西(建構時強制驗證停損)
        -> Risk Engine       要不要開?幾倍槓桿?押多少保證金?
        -> create_paper_trade

倉位大小不再是固定的 1000 USDT。現在由
`權益 × MAX_RISK_PER_TRADE_PCT ÷ 停損距離` 反推 ——
停損放得越遠,倉位越小,每一筆承擔的風險金額才會一致。

槓桿也不再由信心分數決定,改由停損距離與波動度決定,
而且保證強平價永遠比停損遠。
"""
from agmcis.core.errors import TradingRuleViolation
from agmcis.core.models import TradeIntent
from agmcis.config import settings
from database_service import get_open_trade, get_open_trades
from direction import LONG, SHORT
from logger_service import logger
from notifier import notify_open_trade
from paper_trading import create_paper_trade
from risk_control import assert_can_open, evaluate_intent
from scanner_service import scan_market

LONG_SIGNALS = {"🟢 Buy", "🟢 Strong Buy"}
SHORT_SIGNALS = {"🔴 Sell", "🔴 Strong Sell"}


def _build_intent(candidate):
    """
    把掃描結果轉成 TradeIntent。

    TradeIntent 在建構時就會驗證停損存在且方向正確,
    所以不合格的候選在這裡就會被擋下,不會進到風控。
    """
    signal = candidate.get("trade_signal")

    if signal in SHORT_SIGNALS:
        direction = SHORT
    elif signal in LONG_SIGNALS:
        direction = LONG
    else:
        return None, "訊號不是明確的買賣"

    try:
        intent = TradeIntent(
            symbol=candidate.get("symbol"),
            market_type="perpetual",
            direction=direction,
            entry=candidate.get("entry_price"),
            stop_loss=candidate.get("stoploss"),
            take_profit=candidate.get("takeprofit"),
            confidence=candidate.get("confidence"),
            strategy="scanner",
            reasons=[candidate.get("blocked_reason")] if candidate.get("blocked_reason") else [],
        )
    except (TradingRuleViolation, ValueError, TypeError) as exc:
        return None, str(exc)

    return intent, None


def run_auto_trader(max_candidates=10):
    # 帳戶層級的閘門先跑 —— 它很便宜(只查資料庫),
    # 而掃描要打交易所 API。被擋下時沒必要浪費那些請求。
    allowed, reason, status = assert_can_open()
    if not allowed:
        return {
            "status": "BLOCKED_BY_RISK",
            "reason": reason,
            "blockers": status.get("blockers", []),
        }

    data = scan_market()
    considered = []

    for candidate in data[:max_candidates]:
        symbol = candidate.get("symbol")

        if candidate.get("data_ok") is False:
            continue

        intent, problem = _build_intent(candidate)
        if intent is None:
            if problem and "訊號不是" not in problem:
                logger.info("Auto Trader | SKIP | %s | %s", symbol, problem)
            continue

        if get_open_trade(symbol):
            continue

        considered.append(symbol)

        # ---------- HARD GATE:風控決定要不要開、開多大、幾倍 ----------
        decision = evaluate_intent(
            intent,
            atr=candidate.get("indicators", {}).get("atr"),
            mtf_score=candidate.get("mtf_score"),
        )

        if not decision.approved:
            # 帳戶層級的封鎖對所有標的都一樣,沒必要再試下一檔
            if decision.blockers and decision.blockers[0] not in (
                "DUPLICATE_POSITION", "SIZING_REJECTED", "LIQUIDATION_BEFORE_STOP"
            ):
                logger.warning(
                    "Auto Trader | BLOCKED_BY_RISK | %s", decision.reason,
                )
                return {
                    "status": "BLOCKED_BY_RISK",
                    "reason": decision.reason,
                    "blockers": decision.blockers,
                }

            logger.info(
                "Auto Trader | REJECTED | %s | %s", symbol, decision.reason,
            )
            continue

        result = create_paper_trade(
            symbol=symbol,
            entry_price=intent.entry,
            signal=intent.direction.value,
            size_usdt=decision.size_usdt,
            stoploss=intent.stop_loss,
            takeprofit=intent.take_profit,
            leverage=decision.leverage,
            source="AUTO",
        )

        logger.info(
            "Auto Trader | OPEN_ATTEMPT | %s | %s | size=%.2f lev=%gx 風險=%.2f | %s",
            symbol, intent.direction.value, decision.size_usdt,
            decision.leverage, decision.risk_usdt or 0, result.get("message"),
        )

        if result.get("success"):
            notify_open_trade(
                symbol, intent.direction.value, intent.entry,
                intent.stop_loss, intent.take_profit,
                leverage=decision.leverage,
                confidence=intent.confidence,
                mtf_status=candidate.get("mtf_status"),
            )
            return {
                "status": "OPENED",
                "symbol": symbol,
                "decision": decision.to_dict(),
                "result": result,
            }

        # 開倉被拒(停損無效、已有持倉等)不算致命,換下一個候選
        continue

    logger.info("Auto Trader | NO_TRADE_SIGNAL | 評估過 %d 檔", len(considered))
    return {"status": "NO_TRADE_SIGNAL", "considered": considered}
