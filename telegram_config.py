"""
Telegram 設定與來源授權的單一來源。

修正兩個問題:
  1. 環境變數命名不一致 —— config.py 讀 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID,
     notifier.py 與 telegram_commands.py 讀 BOT_TOKEN / CHAT_ID,
     .env.example 只寫前者。依 .env 的實際內容,通知可能靜默失效。
     這裡以 TELEGRAM_* 為正式名稱,並向下相容舊名。
  2. 沒有來源授權 —— listener 原本對任何人的訊息都照做,
     等於任何 Telegram 使用者都能發 /emergency 或 /close 控制這套系統。
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _env(*names, default=""):
    """依序尋找多個環境變數名稱,回傳第一個有值的。用來同時支援新舊命名。"""
    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip()
    return default


BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN", "BOT_TOKEN")
CHAT_ID = _env("TELEGRAM_CHAT_ID", "CHAT_ID")

DISABLE_TELEGRAM_PUSH = _env("DISABLE_TELEGRAM_PUSH", default="false").lower() == "true"

API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""


def _parse_ids(raw):
    ids = set()
    for part in (raw or "").replace(" ", "").split(","):
        if part:
            ids.add(part)
    return ids


def allowed_chat_ids():
    """
    可以下指令的 chat id 集合。
    TELEGRAM_ALLOWED_CHAT_IDS 可列多個(逗號分隔);未設定時退回 TELEGRAM_CHAT_ID。
    """
    allowed = _parse_ids(_env("TELEGRAM_ALLOWED_CHAT_IDS"))
    if not allowed and CHAT_ID:
        allowed.add(str(CHAT_ID))
    return allowed


def is_authorized(chat_id) -> bool:
    """
    白名單之外的來源一律拒絕。
    白名單為空時回傳 False —— 沒有設定授權名單就代表沒有人能下指令,
    這比「沒設定就全部放行」安全得多。
    """
    if chat_id is None:
        return False
    return str(chat_id) in allowed_chat_ids()


def is_configured() -> bool:
    return bool(BOT_TOKEN and CHAT_ID)
