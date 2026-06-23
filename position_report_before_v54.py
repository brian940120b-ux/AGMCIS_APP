from portfolio_manager import get_portfolio_summary
from notifier import send_telegram


def send_position_report():

    portfolio = get_portfolio_summary()
    positions = portfolio.get("open_trades", [])
    total_open_upnl = portfolio.get("total_open_upnl", 0)

    if not positions:
        send_telegram("📊 AGMCIS Position Report\n\n目前沒有持倉。")
        return

    ranked = sorted(
        positions,
        key=lambda x: float(x.get("pnl_usdt") or 0),
        reverse=True
    )

    lines = []

    for i, p in enumerate(ranked, start=1):
        symbol = p.get("symbol")
        pnl = p.get("pnl_usdt") or 0
        lines.append(f"{i}. {symbol} : {pnl} USDT")

    msg = f"""
📊 AGMCIS Position Report

總浮盈虧：{total_open_upnl} USDT

持倉排行：
{chr(10).join(lines)}

Paper Trading
"""

    send_telegram(msg)


if __name__ == "__main__":
    send_position_report()
