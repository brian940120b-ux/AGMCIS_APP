import os
import time
import requests
from dotenv import load_dotenv

from telegram_commands import (
    handle_status,
    handle_positions,
    handle_pause,
    handle_resume,
    handle_help,
    handle_report,
    handle_analytics,
    handle_risk,
    handle_health,
    handle_top,
    handle_losers,
    handle_balance,
    handle_scan,
    handle_close,
    handle_confirm_close,
    handle_cancel_close,
    handle_emergency,
    handle_resume_trading,
    handle_journal,
    handle_performance,
    handle_portfolio
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
API = f"https://api.telegram.org/bot{BOT_TOKEN}"

offset = 0

print("AGMCIS Telegram Listener Started")

while True:

    try:

        r = requests.get(
            f"{API}/getUpdates",
            params={
                "offset": offset,
                "timeout": 10
            },
            timeout=15
        )

        data = r.json()

        for update in data.get("result", []):

            offset = update["update_id"] + 1

            msg = update.get("message", {})

            text = msg.get("text", "")

            print("CMD:", text)

            if text == "/status":
                handle_status()

            elif text == "/positions":
                handle_positions()

            elif text == "/pause":
                handle_pause()

            elif text == "/resume":
                handle_resume()

            elif text == "/help":
                handle_help()

            elif text == "/report":
                handle_report()

            elif text == "/analytics":
                handle_analytics()

            elif text == "/risk":
                handle_risk()

            elif text == "/health":
                handle_health()

            elif text == "/top":
                handle_top()

            elif text == "/losers":
                handle_losers()

            elif text == "/balance":
                handle_balance()

            elif text == "/scan":
                handle_scan()

            elif text.startswith("/close "):
                symbol = text.replace("/close ", "").strip().upper()
                if "/" not in symbol:
                    symbol = symbol.replace("USDT", "") + "/USDT"
                handle_close(symbol)

            elif text.startswith("/confirm_close "):
                symbol = text.replace("/confirm_close ", "").strip().upper()
                if "/" not in symbol:
                    symbol = symbol.replace("USDT", "") + "/USDT"
                handle_confirm_close(symbol)

            elif text == "/cancel_close":
                handle_cancel_close()

            elif text == "/emergency":
                handle_emergency()

            elif text == "/resume_trading":
                handle_resume_trading()

            elif text == "/journal":
                handle_journal()

            elif text == "/performance":
                handle_performance()

            elif text == "/portfolio":
                handle_portfolio()

            elif text.startswith("/confirm_close "):
                symbol = text.replace("/confirm_close ", "").strip().upper()
                if "/" not in symbol:
                    symbol = symbol.replace("USDT", "") + "/USDT"
                handle_confirm_close(symbol)

            elif text == "/cancel_close":
                handle_cancel_close()

            elif text == "/emergency":
                handle_emergency()

            elif text == "/resume_trading":
                handle_resume_trading()

            elif text == "/journal":
                handle_journal()

            elif text == "/performance":
                handle_performance()

            elif text == "/portfolio":
                handle_portfolio()

            elif text.startswith("/confirm_close "):
                symbol = text.replace("/confirm_close ", "").strip().upper()
                if "/" not in symbol:
                    symbol = symbol.replace("USDT", "") + "/USDT"
                handle_confirm_close(symbol)

            elif text == "/cancel_close":
                handle_cancel_close()

            elif text == "/emergency":
                handle_emergency()

            elif text == "/resume_trading":
                handle_resume_trading()

            elif text == "/journal":
                handle_journal()

            elif text == "/performance":
                handle_performance()

            elif text == "/portfolio":
                handle_portfolio()

            elif text.startswith("/close "):
                symbol = text.replace("/close ", "").strip().upper()
                if "/" not in symbol:
                    symbol = symbol.replace("USDT", "") + "/USDT"
                handle_close(symbol)

            elif text.startswith("/confirm_close "):
                symbol = text.replace("/confirm_close ", "").strip().upper()
                if "/" not in symbol:
                    symbol = symbol.replace("USDT", "") + "/USDT"
                handle_confirm_close(symbol)

            elif text == "/cancel_close":
                handle_cancel_close()

            elif text == "/emergency":
                handle_emergency()

            elif text == "/resume_trading":
                handle_resume_trading()

            elif text == "/journal":
                handle_journal()

            elif text == "/performance":
                handle_performance()

            elif text == "/portfolio":
                handle_portfolio()

            elif text.startswith("/confirm_close "):
                symbol = text.replace("/confirm_close ", "").strip().upper()
                if "/" not in symbol:
                    symbol = symbol.replace("USDT", "") + "/USDT"
                handle_confirm_close(symbol)

            elif text == "/cancel_close":
                handle_cancel_close()

            elif text == "/emergency":
                handle_emergency()

            elif text == "/resume_trading":
                handle_resume_trading()

            elif text == "/journal":
                handle_journal()

            elif text == "/performance":
                handle_performance()

            elif text == "/portfolio":
                handle_portfolio()

            elif text.startswith("/confirm_close "):
                symbol = text.replace("/confirm_close ", "").strip().upper()
                if "/" not in symbol:
                    symbol = symbol.replace("USDT", "") + "/USDT"
                handle_confirm_close(symbol)

            elif text == "/cancel_close":
                handle_cancel_close()

            elif text == "/emergency":
                handle_emergency()

            elif text == "/resume_trading":
                handle_resume_trading()

            elif text == "/journal":
                handle_journal()

            elif text == "/performance":
                handle_performance()

            elif text == "/portfolio":
                handle_portfolio()

    except Exception as e:
        print("ERROR:", e)

    time.sleep(2)
