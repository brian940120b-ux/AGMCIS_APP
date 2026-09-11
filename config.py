"""
向下相容 shim。

實際設定已集中到 agmcis/config/settings.py(Phase 1)。
這個檔案保留原本的模組層名稱,讓既有呼叫端
(market_data.py / exchange_engine.py / analytics.py / v2 / v3)完全不用改。

新程式碼請直接用:
    from agmcis.config import settings
"""
from agmcis.config.settings import (  # noqa: F401
    ALLOW_PUBLIC_DATA_ONLY,
    APP_NAME,
    CACHE_TTL,
    DATA_DIR,
    EXCHANGE,
    EXCHANGE_CREDENTIALS,
    EXCHANGE_MAX_RETRIES,
    EXCHANGE_RETRY_BACKOFF_SECONDS,
    EXCHANGE_TIMEOUT_MS,
    MARKET_TYPE,
    PAPER_START_BALANCE,
    VERSION,
    WATCHLIST_SYMBOLS,
    WEBSOCKET_INTERVAL,
)

from agmcis.config import settings

DATA_DIR = str(DATA_DIR)
TELEGRAM_BOT_TOKEN = settings.telegram_bot_token()
TELEGRAM_CHAT_ID = settings.telegram_chat_id()
