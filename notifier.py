import os
import requests
from logger_service import logger
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")


def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        logger.error("Telegram 設定不完整，請檢查 .env")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

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
