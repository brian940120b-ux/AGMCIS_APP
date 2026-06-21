from database_service import get_account, get_open_trades
from portfolio_manager import get_portfolio_summary
from risk_control import get_risk_control_status
from notifier import send_telegram


def send_daily_report():

    a = get_account()
    open_trades = get_open_trades()
    portfolio = get_portfolio_summary()
    risk = get_risk_control_status()

    positions = portfolio.get("open_trades", [])
    total_open_upnl = portfolio.get("total_open_upnl", 0)

    best = "-"
    worst = "-"

    if positions:
        ranked = sorted(
            positions,
            key=lambda x: float(x.get("pnl_usdt") or 0),
            reverse=True
        )

        best = ranked[0].get("symbol")
        worst = ranked[-1].get("symbol")

    msg = f"""
📊 AGMCIS Daily Report Pro

資金：{a.get('balance')} USDT
交易：{a.get('trades')}
勝場：{a.get('wins')}
敗場：{a.get('losses')}

目前持倉：{len(open_trades)}
總浮盈虧：{total_open_upnl} USDT
系統狀態：{risk.get('system_status')}

最佳持倉：{best}
最差持倉：{worst}

Paper Trading
"""

    send_telegram(msg)


if __name__ == "__main__":
    send_daily_report()
