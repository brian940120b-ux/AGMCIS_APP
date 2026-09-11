"""
Risk Engine。

Phase 0.5 起,這是唯一的風控判斷入口,且所有開倉路徑都必須先問過它。
`assert_can_open()` 是給自動交易用的 hard gate —— 它不回傳建議,而是直接決定放行或擋下。

參數全部來自 risk_limits(環境變數),不再硬編碼。
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
    任何自動交易路徑都必須先呼叫這個函式並尊重結果 —— 這是 Phase 0.5 修掉的
    「auto_trader 完全不查風控」問題的補丁。
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
