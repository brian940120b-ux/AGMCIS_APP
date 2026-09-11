"""
Telegram 推播。

設定一律由 telegram_config 提供 —— 原本這裡讀 BOT_TOKEN / CHAT_ID,
而 config.py 讀 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID,.env.example 只寫後者,
造成依 .env 內容不同,通知可能靜默失效。
"""
import requests

from logger_service import logger
from telegram_config import (
    API_BASE,
    BOT_TOKEN,
    CHAT_ID,
    DISABLE_TELEGRAM_PUSH,
    is_configured,
)


def send_telegram(message):
    if DISABLE_TELEGRAM_PUSH:
        return False
    if not is_configured():
        logger.error(
            "Telegram 設定不完整。請在 .env 設定 TELEGRAM_BOT_TOKEN 與 TELEGRAM_CHAT_ID。"
        )
        return False

    url = f"{API_BASE}/sendMessage"

    data = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }

    try:
        response = requests.post(url, data=data, timeout=10)

        if response.status_code == 200:
            return True
        else:
            logger.error(f"Telegram 發送失敗： {response.text}")
            return False

    except Exception as e:
        logger.exception(f"Telegram 發送錯誤： {e}")
        return False
def notify_open_trade(symbol, signal, entry, sl, tp, leverage=None, confidence=None, mtf_status=None):
    msg = (
        "🤖 <b>AGMCIS V87 開倉通知</b>\n\n"
        f"📈 幣種：{symbol}\n"
        f"📊 方向：{signal}\n"
        f"⚙️ 槓桿：{leverage if leverage is not None else '-'}x\n"
        f"🧠 信心：{confidence if confidence is not None else '-'}%\n"
        f"⏱ MTF：{mtf_status if mtf_status is not None else '-'}\n\n"
        f"💰 進場：{entry}\n"
        f"🛑 止損：{sl}\n"
        f"🎯 止盈：{tp}"
    )
    return send_telegram(msg)

def notify_close_trade(symbol, signal, exit_price, pnl_pct=None, pnl_usdt=None, reason=None):
    msg = (
        "🤖 <b>AGMCIS V87 平倉通知</b>\n\n"
        f"📈 幣種：{symbol}\n"
        f"📊 方向：{signal}\n"
        f"💰 出場：{exit_price}\n"
        f"📉 PnL：{pnl_pct if pnl_pct is not None else '-'}%\n"
        f"💵 損益：{pnl_usdt if pnl_usdt is not None else '-'} USDT\n"
        f"📌 原因：{reason if reason else '-'}"
    )
    return send_telegram(msg)
