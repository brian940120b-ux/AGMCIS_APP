"""
已開倉部位的 AI 評估(Dashboard 的 AI Decision Center)。

Phase 9 重寫。舊版是**第三套**獨立的評分:自己一套 score_rsi / score_trend /
score_macd / score_atr 加權出 confidence,再丟給 decision_engine 產生訊號字串。
那套權重跟訊號管線的權重不一樣,也跟風控的判斷不一樣 ——
同一個部位在三個地方會得到三種不同的結論。

現在改成:**跑同一套 Agent 群**,只是帶上部位資訊。
Dashboard 看到的評估,就是系統內部真正在用的評估。

這一層**不下單、不平倉**。它只呈現意見。
真正的出場動作要等 Phase 12 的 Execution Engine。
"""
import logging
import time

from agmcis.agents.base import Vote
from agmcis.core.enums import Direction
from agmcis.signal import agent_pipeline
from database_service import get_open_trades
from direction import is_long, is_short
from ranking_engine import rank_decisions

logger = logging.getLogger("agmcis.ai_decision")

LAST_TOP3_LOG = {"summary": None, "ts": 0}
TOP3_LOG_INTERVAL_SECONDS = 60

# 部位管理的訊號詞彙。與進場訊號刻意分開 ——
# 「要不要開新倉」和「手上這張要不要動」是兩個問題。
NO_DATA = "⚪ No Data"
STOPLOSS_WARNING = "🔴 Stoploss Warning"
REDUCE = "🟠 Reduce Position"
HOLD_NEUTRAL = "🟡 Hold"
HOLD_ALIGNED = "🟢 Hold"


def _position_direction(position):
    signal = position.get("signal") or position.get("direction") or ""
    if is_long(signal):
        return Direction.LONG
    if is_short(signal):
        return Direction.SHORT
    return Direction.WAIT


def _trade_signal(deliberation, position_direction, exit_opinion):
    """
    把 Agent 的意見翻譯成部位管理建議。

    優先順序是刻意的:**風險先於機會**。
    停損快到了這件事,比「共識還是看多」重要。
    """
    if exit_opinion is not None and exit_opinion.vote is Vote.WAIT:
        if exit_opinion.confidence >= 80:
            return STOPLOSS_WARNING, exit_opinion.reasons
        return REDUCE, exit_opinion.reasons

    direction = deliberation.direction

    if direction.is_directional and position_direction.is_directional:
        if direction is position_direction:
            return HOLD_ALIGNED, deliberation.reasons
        # 共識已經轉向,但這一層不平倉 —— 只提示
        return REDUCE, ["Agent 共識方向與持倉相反"] + list(deliberation.reasons)

    if deliberation.blocked_reason:
        return HOLD_NEUTRAL, [deliberation.blocked_reason]

    return HOLD_NEUTRAL, list(deliberation.reasons)


def _evaluate_position(position):
    symbol = position.get("symbol")

    deliberation, report = agent_pipeline.analyse_symbol(
        symbol, position=position,
    )

    opinions = {o.agent: o for o in deliberation.opinions}
    exit_opinion = opinions.get("exit")

    position_direction = _position_direction(position)

    # 資料壞掉時不給建議。分不出「市場中性」與「資料壞掉」是 Phase 0 的老問題。
    errored = [o for o in deliberation.opinions if o.error]
    all_abstained = all(
        o.vote is Vote.ABSTAIN for o in deliberation.opinions
    )

    if all_abstained:
        return {
            "symbol": symbol,
            "action": position_direction.value,
            "trade_signal": NO_DATA,
            "confidence": None,
            "reason": "所有 Agent 棄權(通常是資料不可用),不給建議",
            "votes": {a: o.vote.value for a, o in opinions.items()},
            "errors": [f"{o.agent}: {o.error}" for o in errored],
            "vetoed": bool(report and report.vetoed),
        }

    trade_signal, reasons = _trade_signal(
        deliberation, position_direction, exit_opinion,
    )

    return {
        "symbol": symbol,
        "action": position_direction.value,
        "trade_signal": trade_signal,
        "confidence": round(deliberation.confidence, 2),
        "reason": "; ".join(reasons[:3]) if reasons else "無特別意見",
        "votes": {a: o.vote.value for a, o in opinions.items()},
        "errors": [f"{o.agent}: {o.error}" for o in errored],
        "vetoed": bool(report and report.vetoed),
        "entry_price": position.get("entry_price"),
        "stop_loss": position.get("stoploss") or position.get("stop_loss"),
        "take_profit": position.get("takeprofit") or position.get("take_profit"),
        "leverage": position.get("leverage"),
    }


def get_ai_decisions():
    results = []

    for position in get_open_trades():
        symbol = position.get("symbol")
        try:
            results.append(_evaluate_position(position))
        except Exception as exc:
            # 一個部位評估失敗不該讓整個面板空白,但也不能假裝它沒問題。
            logger.exception("AI 部位評估失敗 | %s", symbol)
            results.append({
                "symbol": symbol,
                "action": "UNKNOWN",
                "trade_signal": NO_DATA,
                "confidence": None,
                "reason": f"評估失敗:{type(exc).__name__}: {exc}",
                "votes": {},
                "errors": [str(exc)],
                "vetoed": False,
            })

    ranked = rank_decisions(results)
    _log_top3(ranked)
    return ranked


def _log_top3(ranked):
    summary = " | ".join(
        f"{i + 1}. {d.get('symbol')} {d.get('trade_signal')} {d.get('confidence')}"
        for i, d in enumerate(ranked[:3])
    )

    now = time.time()
    if (summary != LAST_TOP3_LOG["summary"]
            or now - LAST_TOP3_LOG["ts"] >= TOP3_LOG_INTERVAL_SECONDS):
        logger.info("持倉評估 | %s", summary or "無持倉")
        LAST_TOP3_LOG["summary"] = summary
        LAST_TOP3_LOG["ts"] = now
