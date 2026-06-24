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
    handle_balance
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

    except Exception as e:
        print("ERROR:", e)

    time.sleep(2)
