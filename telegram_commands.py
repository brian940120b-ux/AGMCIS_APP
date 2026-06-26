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


def handle_balance():

    from database_service import (
        get_account,
        get_open_trades,
        get_closed_trades
    )

    account = get_account()
    open_trades = get_open_trades()
    closed_trades = get_closed_trades()

    from market_data import get_price

    total_upnl = 0

    for t in open_trades:
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

        total_upnl += size*roi/100

    msg = f"""
💰 AGMCIS Balance

帳戶資金：{account.get("balance")} USDT

總浮盈虧：{round(total_upnl,2)} USDT

目前持倉：{len(open_trades)}
已平倉：{len(closed_trades)}

勝場：{account.get("wins")}
敗場：{account.get("losses")}
總交易：{account.get("trades")}
"""

    send_message(msg)


def handle_scan():

    try:
        from opportunity_scanner import scan_opportunities

        result = scan_opportunities()


        msg = "🔍 AGMCIS Scan\n\n"

        if result:
            msg += str(result)
        else:
            msg += "掃描完成，無新訊號"

        send_message(msg)


    except Exception as e:
        send_message(f"❌ Scan Error\n\n{e}")


def handle_close(symbol):
    from database_service import get_open_trades
    from pathlib import Path
    import json

    trades = get_open_trades()

    for t in trades:
        if t.get("symbol") == symbol:
            Path("pending_close.json").write_text(
                json.dumps({"symbol": symbol})
            )
            send_message(
                f"⚠️ 平倉確認\n\n"
                f"幣種：{symbol}\n\n"
                f"如確認要平倉，請輸入：\n"
                f"/confirm_close {symbol.replace('/', '')}"
            )
            return

    send_message(f"❌ 找不到持倉：{symbol}")


def handle_confirm_close(symbol):
    from pathlib import Path
    import json

    p = Path("pending_close.json")

    if not p.exists():
        send_message("❌ 沒有待確認平倉")
        return

    data = json.loads(p.read_text() or "{}")

    if data.get("symbol") != symbol:
        send_message(f"❌ 沒有待確認平倉：{symbol}")
        return

    from market_data import get_price
    from paper_trading import close_paper_trade

    price = get_price(symbol)

    if not price:
        send_message(f"❌ 無法取得價格：{symbol}")
        return

    result = close_paper_trade(
        symbol=symbol,
        exit_price=price,
        close_reason="Telegram 確認手動平倉"
    )

    p.write_text("{}")

    if result.get("success"):
        trade = result.get("trade", {})
        send_message(
            f"✅ 確認平倉完成\n\n"
            f"幣種：{symbol}\n"
            f"出場價：{price}\n"
            f"盈虧：{trade.get('pnl_usdt')} USDT\n"
            f"報酬率：{trade.get('pnl_pct')}%"
        )
    else:
        send_message(
            f"❌ 平倉失敗\n\n"
            f"幣種：{symbol}\n"
            f"原因：{result.get('message')}"
        )


def handle_cancel_close():
    from pathlib import Path

    Path("pending_close.json").write_text("{}")

    send_message("✅ 已取消待確認平倉")


def handle_emergency():

    from pathlib import Path

    Path("emergency.stop").write_text("EMERGENCY")

    send_message(
        "🚨 AGMCIS EMERGENCY MODE\n\n"
        "Scanner OFF\n"
        "New Trades OFF\n"
        "System Protected"
    )


def handle_resume_trading():

    from pathlib import Path

    p = Path("emergency.stop")

    if p.exists():
        p.unlink()

    send_message(
        "✅ AGMCIS Trading Resumed\n\n"
        "Emergency Stop 已解除\n"
        "Scanner 已恢復"
    )


def handle_journal():
    from journal_service import get_recent

    rows = get_recent(10)

    if not rows:
        send_message("📒 目前沒有交易日誌")
        return

    msg = "📒 AGMCIS 最近交易日誌\n\n"

    for r in rows:
        symbol, action, price, reason, created_at = r
        icon = "🟢" if action == "OPEN" else "🔴"

        msg += (
            f"{icon} {action}\n"
            f"幣種：{symbol}\n"
            f"價格：{price}\n"
            f"原因：{reason}\n"
            f"時間：{created_at}\n\n"
        )

    send_message(msg)


def handle_performance():
    from performance_service import get_performance_summary

    p = get_performance_summary()
    best = p.get("best") or {}
    worst = p.get("worst") or {}

    msg = f"""
📊 AGMCIS Performance

總交易：{p.get('total_trades')}
已平倉：{p.get('closed_trades')}
勝率：{p.get('win_rate')}%

總損益：{p.get('total_pnl')} USDT
平均每筆：{p.get('avg_pnl')} USDT

🏆 最佳交易：
{best.get('symbol', '-')} {best.get('pnl_usdt', '-')} USDT

⚠️ 最差交易：
{worst.get('symbol', '-')} {worst.get('pnl_usdt', '-')} USDT
"""
    send_message(msg)

