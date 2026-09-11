"""
AGMCIS 全域設定

專注於 BingX 合約(永續 Swap)交易,不做多交易所備援、不支援現貨。
所有機密一律只從環境變數讀,絕不寫死在程式碼裡。
"""
import os
from dotenv import load_dotenv

load_dotenv()

APP_NAME = "AGMCIS"
VERSION = "1.0.0"

# ---------------- 交易所設定(僅 BingX) ----------------
EXCHANGE = "bingx"

EXCHANGE_CREDENTIALS = {
    "apiKey": os.getenv("BINGX_API_KEY", ""),
    "secret": os.getenv("BINGX_API_SECRET", ""),
}

# 是否允許在沒有 API Key 的情況下,仍以「公開行情端點」運作(看盤/訊號用途不需要 Key)
ALLOW_PUBLIC_DATA_ONLY = os.getenv("ALLOW_PUBLIC_DATA_ONLY", "true").lower() == "true"

# 市場型態固定為合約(USDT-M 永續 Swap)。本系統不處理現貨。
MARKET_TYPE = "swap"

# ---------------- 監控幣種 ----------------
WATCHLIST_SYMBOLS = os.getenv(
    "WATCHLIST_SYMBOLS",
    "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT"
).split(",")

# ---------------- 資料層行為設定 ----------------
# 快取存活秒數,依資料類型不同給不同新鮮度要求
CACHE_TTL = {
    "ticker": 3,          # 即時價格,3 秒內視為新鮮
    "ohlcv": 15,          # K 線,15 秒
    "funding_rate": 60,   # 資金費率,合約特有,變動慢,60 秒
    "open_interest": 30,  # 持倉量,合約特有
}

# 交易所請求逾時(毫秒)與重試設定
EXCHANGE_TIMEOUT_MS = int(os.getenv("EXCHANGE_TIMEOUT_MS", "8000"))
EXCHANGE_MAX_RETRIES = int(os.getenv("EXCHANGE_MAX_RETRIES", "3"))
EXCHANGE_RETRY_BACKOFF_SECONDS = float(os.getenv("EXCHANGE_RETRY_BACKOFF_SECONDS", "0.8"))

# ---------------- Telegram ----------------
# 實際設定與來源授權集中在 telegram_config.py(同時相容舊名 BOT_TOKEN / CHAT_ID)。
# 這裡只保留轉發,避免同一份設定有兩個互相矛盾的來源。
from telegram_config import BOT_TOKEN as TELEGRAM_BOT_TOKEN  # noqa: E402
from telegram_config import CHAT_ID as TELEGRAM_CHAT_ID  # noqa: E402

# ---------------- Paper Trading ----------------
PAPER_START_BALANCE = float(os.getenv("PAPER_START_BALANCE", "10000"))

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
