"""
向下相容 shim。

Telegram 設定與來源授權已集中到 agmcis/config/settings.py(Phase 1)。
新程式碼請直接用 settings。

這裡的模組層常數在 import 時取值,所以 importlib.reload(telegram_config)
會重新從環境變數讀取 —— settings 那一側是函式,不會有快取問題。
"""
from agmcis.config import settings
from agmcis.config.settings import (  # noqa: F401
    telegram_allowed_chat_ids as allowed_chat_ids,
    telegram_is_authorized as is_authorized,
    telegram_is_configured as is_configured,
)

BOT_TOKEN = settings.telegram_bot_token()
CHAT_ID = settings.telegram_chat_id()
API_BASE = settings.telegram_api_base()
DISABLE_TELEGRAM_PUSH = settings.disable_telegram_push()
