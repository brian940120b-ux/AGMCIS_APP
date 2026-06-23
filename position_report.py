from database_service import get_account, get_open_trades
from market_data import get_price
from notifier import send_telegram

LEVERAGE = 3

def trailing_gap(roi):
    if roi >= 20:
        return 2
    if roi >= 10:
        return 3
    if roi >= 5:
        return 4
    return 0

def send_position_report():

    account = get_account()
    positions = get_open_trades()

    if not positions:
        send_telegram("📊 AGMCIS Position Report Pro\n\n目前沒有持倉。")
        return

    lines = []
    total_upnl = 0

    for p in positions:
        symbol = p.get("symbol")
        signal = p.get("signal")
        entry = float(p.get("entry_price") or 0)
        size = float(p.get("size_usdt") or 0)
        stoploss = float(p.get("stoploss") or 0)
        takeprofit = float(p.get("takeprofit") or 0)

        price = get_price(symbol)

        if not price or entry <= 0:
            continue

        price = float(price)

        if signal == "做多":
            raw = (price - entry) / entry * 100
        elif signal == "做空":
            raw = (entry - price) / entry * 100
        else:
            raw = 0

        roi = round(raw * LEVERAGE, 2)
        upnl = round(size * roi / 100, 2)
        total_upnl += upnl

        gap = trailing_gap(roi)
        trailing = f"ON GAP {gap}%" if gap > 0 else "OFF"

        distance_sl = round(abs((price - stoploss) / price * 100), 2) if stoploss > 0 else 0
        distance_tp = round(abs((takeprofit - price) / price * 100), 2) if takeprofit > 0 else 0

        icon = "🟢" if upnl >= 0 else "🔴"

        lines.append(
            f"{icon} {symbol}\n"
            f"ROI：{roi}%\n"
            f"UPNL：{upnl} USDT\n"
            f"Trailing：{trailing}\n"
            f"SL距離：{distance_sl}%\n"
            f"TP距離：{distance_tp}%\n"
        )

    msg = f"""
📊 AGMCIS Position Report Pro

資金：{account.get('balance')} USDT
持倉數：{len(positions)}
總浮盈虧：{round(total_upnl, 2)} USDT

{chr(10).join(lines)}
"""

    send_telegram(msg)


if __name__ == "__main__":
    send_position_report()
