from analytics import get_trade_analytics
from portfolio_manager import get_portfolio_summary


MAX_DRAWDOWN_LIMIT = 15
MAX_EXPOSURE_LIMIT = 80
MAX_OPEN_POSITIONS = 5
MIN_PROFIT_FACTOR = 0.8


def get_risk_control_status():
    analytics = get_trade_analytics()
    portfolio = get_portfolio_summary()

    alerts = []
    allow_new_trade = True
    emergency_stop = False

    max_drawdown = analytics.get("max_drawdown", 0)
    profit_factor = analytics.get("profit_factor", 0)
    exposure_ratio = portfolio.get("exposure_ratio", 0)
    open_positions = portfolio.get("open_positions", 0)

    if max_drawdown >= MAX_DRAWDOWN_LIMIT:
        alerts.append({
            "level": "HIGH",
            "title": "最大回撤過高",
            "message": f"目前最大回撤 {max_drawdown}% ，已超過限制 {MAX_DRAWDOWN_LIMIT}%。"
        })
        allow_new_trade = False
        emergency_stop = True

    if exposure_ratio >= MAX_EXPOSURE_LIMIT:
        alerts.append({
            "level": "HIGH",
            "title": "總曝險過高",
            "message": f"目前曝險比例 {exposure_ratio}% ，已超過限制 {MAX_EXPOSURE_LIMIT}%。"
        })
        allow_new_trade = False

    if open_positions >= MAX_OPEN_POSITIONS:
        alerts.append({
            "level": "MEDIUM",
            "title": "持倉數過多",
            "message": f"目前持倉數 {open_positions} 筆，已達上限 {MAX_OPEN_POSITIONS} 筆。"
        })
        allow_new_trade = False

    if profit_factor != 0 and profit_factor < MIN_PROFIT_FACTOR:
        alerts.append({
            "level": "MEDIUM",
            "title": "Profit Factor 偏低",
            "message": f"目前 Profit Factor {profit_factor} ，低於建議值 {MIN_PROFIT_FACTOR}。"
        })

    if not alerts:
        alerts.append({
            "level": "NORMAL",
            "title": "風控狀態正常",
            "message": "目前最大回撤、曝險比例與持倉數皆在安全範圍內。"
        })

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
        "max_drawdown": max_drawdown,
        "exposure_ratio": exposure_ratio,
        "open_positions": open_positions,
        "profit_factor": profit_factor,
        "alerts": alerts
    }