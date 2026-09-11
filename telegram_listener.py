"""
Telegram 指令監聽。

Phase 0.5 的關鍵修正:加入來源授權。
原本程式從 update 取出 text 就直接分派,完全沒有檢查 chat_id ——
任何找到這個 bot 的 Telegram 使用者都能發 /emergency、/close BTC/USDT、
/confirm_close、/resume_trading 來控制整套交易系統。

同時把原本複製 6 次的 elif 區塊收成一張指令表。
"""
import time

import requests

from logger_service import logger
from telegram_config import API_BASE, BOT_TOKEN, allowed_chat_ids, is_authorized
from telegram_commands import (
    handle_analytics,
    handle_balance,
    handle_cancel_close,
    handle_close,
    handle_confirm_close,
    handle_emergency,
    handle_health,
    handle_help,
    handle_journal,
    handle_losers,
    handle_performance,
    handle_portfolio,
    handle_positions,
    handle_pause,
    handle_report,
    handle_resume,
    handle_resume_trading,
    handle_risk,
    handle_scan,
    handle_status,
    handle_top,
)

POLL_TIMEOUT = 10
SLEEP_SECONDS = 2

# 不帶參數的指令
COMMANDS = {
    "/status": handle_status,
    "/positions": handle_positions,
    "/pause": handle_pause,
    "/resume": handle_resume,
    "/help": handle_help,
    "/report": handle_report,
    "/analytics": handle_analytics,
    "/risk": handle_risk,
    "/health": handle_health,
    "/top": handle_top,
    "/losers": handle_losers,
    "/balance": handle_balance,
    "/scan": handle_scan,
    "/cancel_close": handle_cancel_close,
    "/emergency": handle_emergency,
    "/resume_trading": handle_resume_trading,
    "/journal": handle_journal,
    "/performance": handle_performance,
    "/portfolio": handle_portfolio,
}

# 需要一個 symbol 參數的指令
SYMBOL_COMMANDS = {
    "/close": handle_close,
    "/confirm_close": handle_confirm_close,
}


def normalize_symbol(raw):
    symbol = raw.strip().upper()
    if "/" not in symbol:
        symbol = symbol.replace("USDT", "") + "/USDT"
    return symbol


def dispatch(text):
    text = (text or "").strip()

    if text in COMMANDS:
        COMMANDS[text]()
        return True

    for prefix, handler in SYMBOL_COMMANDS.items():
        if text.startswith(prefix + " "):
            handler(normalize_symbol(text[len(prefix):]))
            return True

    return False


def handle_update(update):
    message = update.get("message") or update.get("edited_message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    text = message.get("text", "")

    if not text:
        return

    if not is_authorized(chat_id):
        # 只記錄 chat_id 與指令名稱,不回覆任何訊息 —— 不要讓未授權的人知道 bot 是活的。
        logger.warning(
            "Telegram | UNAUTHORIZED | chat_id=%s | command=%s",
            chat_id, text.split()[0] if text.split() else "",
        )
        return

    logger.info("Telegram | CMD | chat_id=%s | %s", chat_id, text)

    if not dispatch(text):
        logger.info("Telegram | UNKNOWN_COMMAND | %s", text)


def main():
    if not BOT_TOKEN:
        logger.error("Telegram | BOT_TOKEN 未設定,listener 不啟動")
        return

    allowed = allowed_chat_ids()
    if not allowed:
        logger.error(
            "Telegram | 未設定授權名單。請在 .env 設定 TELEGRAM_CHAT_ID 或 "
            "TELEGRAM_ALLOWED_CHAT_IDS,否則所有指令都會被拒絕。"
        )
    else:
        logger.info("Telegram | 授權 chat 數量:%d", len(allowed))

    logger.info("Telegram Listener 啟動")
    offset = 0

    while True:
        try:
            response = requests.get(
                f"{API_BASE}/getUpdates",
                params={"offset": offset, "timeout": POLL_TIMEOUT},
                timeout=POLL_TIMEOUT + 5,
            )
            data = response.json()

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                try:
                    handle_update(update)
                except Exception as exc:
                    logger.exception("Telegram | 指令執行失敗 | %s", exc)

        except Exception as exc:
            logger.exception("Telegram | 輪詢失敗 | %s", exc)

        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()
