from database_service import get_closed_trades
from notifier import send_telegram


def send_analytics_report():

    trades = get_closed_trades()

    pnls = [
        float(t.get("pnl_usdt") or 0)
        for t in trades
    ]

    total_realized = round(sum(pnls), 2)

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    win_rate = round(len(wins) / len(pnls) * 100, 2) if pnls else 0
    best_trade = max(pnls) if pnls else 0
    worst_trade = min(pnls) if pnls else 0

    msg = f"""
📊 AGMCIS Analytics Report

已平倉交易：{len(trades)}
勝率：{win_rate}%

已實現收益：{total_realized} USDT
最佳交易：{round(best_trade,2)} USDT
最差交易：{round(worst_trade,2)} USDT

Paper Trading
"""

    send_telegram(msg)


if __name__ == "__main__":
    send_analytics_report()
