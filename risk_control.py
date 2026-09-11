"""
向下相容 shim。

實作已移到 agmcis/risk/(Phase 5):
    engine.py           RiskEngine:閘門 + 倉位 + 槓桿的完整裁決
    position_sizing.py  由 權益 × 風險% ÷ 停損距離 反推倉位
    leverage.py         由停損距離與波動度決定槓桿(不是信心分數)
    kill_switch.py      停新單 / 撤單 / 平倉 / 稽核

這裡保留原本的函式名稱,讓既有呼叫端不用改。

新程式碼請直接用:
    from agmcis.risk.engine import get_engine
    decision = get_engine().evaluate(intent, state)
"""
from pathlib import Path

import risk_limits
from analytics import get_trade_analytics
from database_service import (
    count_trades_since,
    get_consecutive_losses,
    get_realized_pnl_since,
)
from logger_service import logger
from portfolio_manager import get_portfolio_summary


def _alert(level, title, message):
    return {"level": level, "title": title, "message": message}


def get_risk_control_status():
    analytics = get_trade_analytics()
    portfolio = get_portfolio_summary()

    alerts = []
    blockers = []
    emergency_stop = False

    if Path(risk_limits.EMERGENCY_STOP_FILE).exists():
        alerts.append(_alert("HIGH", "緊急停止", "偵測到 emergency.stop,暫停新開倉。"))
        blockers.append("EMERGENCY_STOP_FILE")
        emergency_stop = True

    if Path(risk_limits.TRADING_PAUSE_FILE).exists():
        alerts.append(_alert("MEDIUM", "交易暫停", "偵測到 trading_pause.flag,暫停新開倉。"))
        blockers.append("TRADING_PAUSE_FLAG")

    max_drawdown = analytics.get("max_drawdown", 0)
    profit_factor = analytics.get("profit_factor", 0)
    exposure_ratio = portfolio.get("exposure_ratio", 0)
    open_positions = portfolio.get("open_positions", 0)
    total_open_upnl = portfolio.get("total_open_upnl", 0)

    daily_pnl = get_realized_pnl_since(24)
    trades_today = count_trades_since(24)
    consecutive_losses = get_consecutive_losses()

    if max_drawdown >= risk_limits.MAX_DRAWDOWN_PCT:
        alerts.append(_alert(
            "HIGH", "最大回撤過高",
            f"目前最大回撤 {max_drawdown}%,已超過限制 {risk_limits.MAX_DRAWDOWN_PCT}%。",
        ))
        blockers.append("MAX_DRAWDOWN")
        emergency_stop = True

    if exposure_ratio >= risk_limits.MAX_EXPOSURE_PCT:
        alerts.append(_alert(
            "HIGH", "總曝險過高",
            f"目前曝險 {exposure_ratio}%,已超過限制 {risk_limits.MAX_EXPOSURE_PCT}%。",
        ))
        blockers.append("MAX_EXPOSURE")

    if open_positions >= risk_limits.MAX_OPEN_POSITIONS:
        alerts.append(_alert(
            "MEDIUM", "持倉數已達上限",
            f"目前 {open_positions} 筆,上限 {risk_limits.MAX_OPEN_POSITIONS} 筆。",
        ))
        blockers.append("MAX_OPEN_POSITIONS")

    if total_open_upnl <= risk_limits.MAX_TOTAL_OPEN_LOSS_USDT:
        alerts.append(_alert(
            "HIGH", "總浮虧過高",
            f"目前總浮虧 {total_open_upnl} USDT,已低於限制 "
            f"{risk_limits.MAX_TOTAL_OPEN_LOSS_USDT} USDT。",
        ))
        blockers.append("MAX_TOTAL_OPEN_LOSS")

    if daily_pnl <= -abs(risk_limits.MAX_DAILY_LOSS_USDT):
        alerts.append(_alert(
            "HIGH", "單日虧損達上限",
            f"近 24 小時已實現損益 {round(daily_pnl, 2)} USDT,"
            f"已達單日虧損上限 {risk_limits.MAX_DAILY_LOSS_USDT} USDT。",
        ))
        blockers.append("MAX_DAILY_LOSS")
        emergency_stop = True

    if consecutive_losses >= risk_limits.MAX_CONSECUTIVE_LOSSES:
        alerts.append(_alert(
            "HIGH", "連續虧損熔斷",
            f"已連續虧損 {consecutive_losses} 筆,達熔斷門檻 "
            f"{risk_limits.MAX_CONSECUTIVE_LOSSES} 筆。",
        ))
        blockers.append("MAX_CONSECUTIVE_LOSSES")

    if trades_today >= risk_limits.MAX_TRADES_PER_DAY:
        alerts.append(_alert(
            "MEDIUM", "單日交易次數達上限",
            f"近 24 小時已開倉 {trades_today} 筆,上限 "
            f"{risk_limits.MAX_TRADES_PER_DAY} 筆。",
        ))
        blockers.append("MAX_TRADES_PER_DAY")

    if profit_factor != 0 and profit_factor < risk_limits.MIN_PROFIT_FACTOR:
        alerts.append(_alert(
            "MEDIUM", "Profit Factor 偏低",
            f"目前 Profit Factor {profit_factor},低於建議值 "
            f"{risk_limits.MIN_PROFIT_FACTOR}。",
        ))

    allow_new_trade = not blockers

    if not alerts:
        alerts.append(_alert("NORMAL", "風控狀態正常", "回撤、曝險、持倉數與虧損額度皆在安全範圍內。"))

    if emergency_stop:
        system_status = "EMERGENCY_STOP"
    elif allow_new_trade:
        system_status = "ACTIVE"
    else:
        system_status = "LIMITED"

    return {
        "system_status": system_status,
        "allow_new_trade": allow_new_trade,
        "emergency_stop": emergency_stop,
        "blockers": blockers,
        "max_drawdown": max_drawdown,
        "exposure_ratio": exposure_ratio,
        "open_positions": open_positions,
        "profit_factor": profit_factor,
        "total_open_upnl": total_open_upnl,
        "daily_realized_pnl": round(daily_pnl, 2),
        "trades_last_24h": trades_today,
        "consecutive_losses": consecutive_losses,
        "limits": risk_limits.as_dict(),
        "alerts": alerts,
    }


def cap_leverage(requested_leverage):
    """槓桿上限由風控決定,任何策略或 Agent 都不能超過。"""
    try:
        requested = float(requested_leverage)
    except (TypeError, ValueError):
        requested = 1.0
    return max(1.0, min(requested, risk_limits.MAX_LEVERAGE))


def assert_can_open(symbol=None):
    """
    自動開倉前的 hard gate。

    回傳 (allowed: bool, reason: str|None, status: dict)。
    任何自動交易路徑都必須先呼叫這個函式並尊重結果。

    ⚠️ 這只是帳戶層級的閘門,**不決定倉位大小**。
    需要完整裁決(含 sizing 與槓桿)請用 evaluate_intent()。
    """
    status = get_risk_control_status()

    if not status["allow_new_trade"]:
        reason = ",".join(status["blockers"])
        logger.warning(
            "Risk gate BLOCKED | symbol=%s | status=%s | blockers=%s",
            symbol, status["system_status"], reason,
        )
        return False, reason, status

    logger.info("Risk gate PASSED | symbol=%s | status=%s", symbol, status["system_status"])
    return True, None, status


def evaluate_intent(intent, atr=None, mtf_score=None,
                    contract_max_leverage=None, min_notional=None):
    """
    完整風險裁決:閘門 + 槓桿 + 倉位大小。

    這是 Phase 5 之後的正式入口。回傳 RiskDecision ——
    approved=False 時 size_usdt 與 leverage 沒有意義。

    倉位由 `權益 × MAX_RISK_PER_TRADE_PCT ÷ 停損距離` 決定,
    不再是固定的 1000 USDT。
    """
    from agmcis.risk.account_state import build_account_state
    from agmcis.risk.engine import get_engine

    state = build_account_state()

    return get_engine().evaluate(
        intent, state,
        atr=atr, mtf_score=mtf_score,
        contract_max_leverage=contract_max_leverage,
        min_notional=min_notional,
        correlation=_correlation_for(intent, state),
    )


def _correlation_for(intent, state):
    """
    組合風險要用的相關係數矩陣。

    取不到就回 None —— portfolio.assess() 收到 None 會把所有同向部位
    當成同一群,也就是最保守的那一邊。這裡刻意不讓失敗變成「不相關」。
    """
    from agmcis.risk import correlation

    symbols = list(dict.fromkeys([intent.symbol] + list(state.open_symbols or [])))

    if len(symbols) < 2:
        return None      # 只有一檔的時候沒有相關性可言

    try:
        return correlation.get_matrix(symbols)
    except Exception as exc:
        logger.warning("相關係數取得失敗,改用保守假設 | %s", exc)
        return None
