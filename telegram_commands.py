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

def handle_pause():
    from system_control import pause_system
    pause_system()
    send_message("⏸ AGMCIS 已暫停自動開倉\n\n持倉管理、停損、Trailing Stop 仍會繼續運作。")

def handle_resume():
    from system_control import resume_system
    resume_system()
    send_message("▶️ AGMCIS 已恢復自動開倉")

def handle_report():
    from position_report import send_position_report
    send_position_report()

def handle_help():
    msg = """
🤖 AGMCIS Command Center

/status - 系統狀態
/positions - 目前持倉
/report - 持倉完整報告
/pause - 暫停自動開倉
/resume - 恢復自動開倉
/help - 指令說明

AGMCIS V62 Pro
"""
    send_message(msg)

def handle_analytics():
    import requests

    data = requests.get(
        "http://127.0.0.1:8000/api/analytics_pro",
        timeout=10
    ).json()

    msg = f"""
📊 AGMCIS Analytics

已實現收益：{data.get('total_realized')} USDT
最大連勝：{data.get('max_win_streak')}
最大連敗：{data.get('max_loss_streak')}
已平倉交易：{data.get('closed_trades')}

AGMCIS V62 Pro
"""
    send_message(msg)

def handle_risk():
    import requests

    data = requests.get(
        "http://127.0.0.1:8000/api/dashboard",
        timeout=10
    ).json()

    msg = f"""
⚠️ AGMCIS Risk Center

風險等級：{data.get('risk_level')}
系統狀態：{data.get('system_status')}

持倉數量：{data.get('open_count')}
已平倉：{data.get('closed_count')}

總浮盈虧：
{data.get('total_open_upnl')} USDT

AGMCIS V62 Pro
"""

    send_message(msg)


def handle_health():
    import subprocess

    services = [
        "agmcis",
        "agmcis-position",
        "agmcis-opportunity",
        "agmcis-telegram-listener"
    ]

    lines = ["🟢 AGMCIS Health\n"]

    for svc in services:
        try:
            status = subprocess.check_output(
                ["systemctl","is-active",svc],
                text=True
            ).strip()

            icon = "🟢" if status == "active" else "🔴"

            lines.append(
                f"{icon} {svc} : {status}"
            )

        except:
            lines.append(
                f"🔴 {svc} : error"
            )

    send_message("\n".join(lines))


def handle_top():

    from database_service import get_open_trades
    from market_data import get_price

    trades = get_open_trades()

    if not trades:
        send_message("目前沒有持倉")
        return

    rows = []

    for t in trades:

        symbol = t.get("symbol")
        signal = t.get("signal")
        entry = float(t.get("entry_price") or 0)
        size = float(t.get("size_usdt") or 0)

        price = get_price(symbol)

        if not price or entry <= 0:
            continue

        price = float(price)

        if signal == "做多":
            roi = (price-entry)/entry*100*3
        else:
            roi = (entry-price)/entry*100*3

        upnl = size*roi/100

        rows.append((upnl,symbol,roi))

    rows.sort(reverse=True)

    msg = "🏆 Top Positions\n\n"

    for i,r in enumerate(rows[:5],1):
        msg += f"{i}. {r[1]}\nROI {round(r[2],2)}%\nUPNL {round(r[0],2)} USDT\n\n"

    send_message(msg)


def handle_top():

    from database_service import get_open_trades
    from market_data import get_price

    trades = get_open_trades()

    if not trades:
        send_message("目前沒有持倉")
        return

    rows = []

    for t in trades:

        symbol = t.get("symbol")
        signal = t.get("signal")
        entry = float(t.get("entry_price") or 0)
        size = float(t.get("size_usdt") or 0)

        price = get_price(symbol)

        if not price or entry <= 0:
            continue

        price = float(price)

        if signal == "做多":
            roi = (price-entry)/entry*100*3
        else:
            roi = (entry-price)/entry*100*3

        upnl = size*roi/100

        rows.append((upnl,symbol,roi))

    rows.sort(reverse=True)

    msg = "🏆 Top Positions\n\n"

    for i,r in enumerate(rows[:5],1):
        msg += f"{i}. {r[1]}\nROI {round(r[2],2)}%\nUPNL {round(r[0],2)} USDT\n\n"

    send_message(msg)

def handle_losers():

    from database_service import get_open_trades
    from market_data import get_price

    trades = get_open_trades()

    if not trades:
        send_message("目前沒有持倉")
        return

    rows = []

    for t in trades:

        symbol = t.get("symbol")
        signal = t.get("signal")
        entry = float(t.get("entry_price") or 0)
        size = float(t.get("size_usdt") or 0)

        price = get_price(symbol)

        if not price or entry <= 0:
            continue

        price = float(price)

        if signal == "做多":
            roi = (price-entry)/entry*100*3
        else:
            roi = (entry-price)/entry*100*3

        upnl = size*roi/100

        rows.append((upnl,symbol,roi))

    rows.sort()

    msg = "⚠️ Worst Positions\n\n"

    for i,r in enumerate(rows[:5],1):
        msg += f"{i}. {r[1]}\nROI {round(r[2],2)}%\nUPNL {round(r[0],2)} USDT\n\n"

    send_message(msg)

