import os
import time
import requests
from dotenv import load_dotenv
from database_service import get_account, get_open_trades
from portfolio_manager import get_portfolio_summary
from risk_control import get_risk_control_status

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = str(os.getenv("CHAT_ID")).strip()

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

def send_message(text):
    requests.post(
        f"{API}/sendMessage",
        data={
            "chat_id": CHAT_ID,
            "text": text
        },
        timeout=10
    )

def handle_status():
    account = get_account()
    portfolio = get_portfolio_summary()
    risk = get_risk_control_status()

    msg = f"""
📊 AGMCIS Status

資金：{account.get('balance')} USDT
目前持倉：{portfolio.get('open_positions')}
總浮盈虧：{portfolio.get('total_open_upnl')} USDT
系統狀態：{risk.get('system_status')}

Paper Trading
"""
    send_message(msg)

if __name__ == "__main__":
    handle_status()


def handle_positions():

    from database_service import get_open_trades
    from market_data import get_price

    trades = get_open_trades()

    if not trades:
        send_message("目前沒有持倉")
        return

    lines = []

    for t in trades:
        symbol = t.get("symbol")
        signal = t.get("signal")
        entry = float(t.get("entry_price") or 0)
        size = float(t.get("size_usdt") or 0)
        sl = t.get("stoploss")
        tp = t.get("takeprofit")
        price = get_price(symbol)

        if not price or entry <= 0:
            continue

        price = float(price)

        if signal == "做多":
            roi = (price - entry) / entry * 100 * 3
        else:
            roi = (entry - price) / entry * 100 * 3

        roi = round(roi, 2)
        upnl = round(size * roi / 100, 2)

        if roi >= 20:
            trailing = "ON GAP 2%"
        elif roi >= 10:
            trailing = "ON GAP 3%"
        elif roi >= 5:
            trailing = "ON GAP 4%"
        else:
            trailing = "OFF"

        icon = "🟢" if upnl >= 0 else "🔴"

        lines.append(
            f"{icon} {symbol}\n"
            f"方向：{signal}\n"
            f"ROI：{roi}%\n"
            f"UPNL：{upnl} USDT\n"
            f"Trailing：{trailing}\n"
            f"SL：{sl}\n"
            f"TP：{tp}\n"
        )

    send_message(
        "📈 AGMCIS Positions Pro\n\n" +
        "\n".join(lines)
    )
