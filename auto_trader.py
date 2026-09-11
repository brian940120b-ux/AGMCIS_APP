"""
自動交易。

Phase 9 之後的完整鏈路:

    scan_market()            決定要看哪些標的(掃描層)
        -> Agent 群          十二個 Agent 各自出意見
        -> Consensus         彙總成 TradeIntent(建構時強制驗證停損)
        -> Supervisor        Agent 群本身可不可信?可否決,不可製造交易
        -> Risk Engine       要不要開?幾倍槓桿?押多少保證金?
        -> Trading Rules     交易所收不收這組數字?
        -> Execution Engine  送單、狀態機、**開倉後必須有停損保護**

**TradeIntent 的產生者只有一個,就是 Agent 共識。**
掃描層負責的是「看哪些標的」,不是「要不要進場」——
Phase 6 合併過兩條互相矛盾的訊號管線,不能在這裡又長出第二條。

倉位大小不再是固定的 1000 USDT。現在由
`權益 × MAX_RISK_PER_TRADE_PCT ÷ 停損距離` 反推 ——
停損放得越遠,倉位越小,每一筆承擔的風險金額才會一致。

槓桿也不再由信心分數決定,改由停損距離與波動度決定,
而且保證強平價永遠比停損遠。
"""
from agmcis.execution import engine as execution
from agmcis.signal import agent_pipeline
from database_service import get_open_trade
from logger_service import logger
from notifier import notify_open_trade
from risk_control import assert_can_open, evaluate_intent
from scanner_service import scan_market


def _agent_intent(symbol, timeframe=None):
    """
    跑 Agent 共識,回傳 (TradeIntent | None, 說明)。

    Agent 層只產生意圖。它拿不到交易所連線,也決定不了部位大小 ——
    那是 Risk Engine 的職責(Master Prompt 第十九、三十二節)。
    """
    kwargs = {"timeframe": timeframe} if timeframe else {}

    try:
        deliberation, report = agent_pipeline.analyse_symbol(symbol, **kwargs)
    except Exception as exc:
        # Agent 層出錯不能讓整個自動交易掛掉,但也不能安靜地跳過。
        logger.exception("Auto Trader | AGENT_ERROR | %s", symbol)
        return None, f"Agent 層失敗:{type(exc).__name__}: {exc}"

    if report is not None and report.health_warnings:
        for warning in report.health_warnings:
            logger.warning("Auto Trader | AGENT_HEALTH | %s | %s", symbol, warning)

    if deliberation.intent is None:
        return None, deliberation.blocked_reason or "Agent 共識結論是觀望"

    return deliberation.intent, None


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

        # 已有部位就不必浪費 Agent 與交易所的請求
        if get_open_trade(symbol):
            continue

        considered.append(symbol)

        intent, problem = _agent_intent(symbol)
        if intent is None:
            logger.info("Auto Trader | NO_INTENT | %s | %s", symbol, problem)
            continue

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

        # ---------- Execution Engine:送單 + 狀態機 + 停損保護 ----------
        result = execution.get_engine().execute(decision)

        logger.info(
            "Auto Trader | EXECUTE | %s | %s | size=%.2f lev=%gx 風險=%.2f | %s | %s",
            symbol, intent.direction.value, decision.size_usdt,
            decision.leverage, decision.risk_usdt or 0,
            result.status, result.reason or "",
        )

        if result.naked_position_closed:
            # 開了倉但沒有停損保護,已經被緊急平掉。
            # 這不是「換下一個候選」的小事 —— 這一輪直接停,讓人去看為什麼。
            logger.critical(
                "Auto Trader | NAKED_POSITION | %s | 已緊急平倉,本輪中止", symbol,
            )
            return {
                "status": "NAKED_POSITION_CLOSED",
                "symbol": symbol,
                "reason": result.reason,
                "execution": result.to_dict(),
            }

        if result.ok:
            notify_open_trade(
                symbol, intent.direction.value,
                result.fill_price or intent.entry,
                intent.stop_loss, intent.take_profit,
                leverage=decision.leverage,
                confidence=intent.confidence,
                mtf_status=candidate.get("mtf_status"),
            )
            return {
                "status": "OPENED",
                "symbol": symbol,
                "decision": decision.to_dict(),
                "execution": result.to_dict(),
            }

        # 送單被拒(規則不符、已有持倉等)不算致命,換下一個候選
        continue

    logger.info("Auto Trader | NO_TRADE_SIGNAL | 評估過 %d 檔", len(considered))
    return {"status": "NO_TRADE_SIGNAL", "considered": considered}
