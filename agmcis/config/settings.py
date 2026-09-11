"""
AGMCIS 組態 —— 單一來源。

Phase 1 把原本散在 config.py、risk_limits.py、telegram_config.py 與各模組
硬編碼常數裡的設定全部集中到這裡。根目錄那三個檔案現在是 re-export shim,
生產服務的 import 路徑完全不用改。

三個原則:
  1. 機密一律只從環境變數讀,絕不寫死。
  2. 沒有設定金鑰時,預設是「拒絕」而不是「放行」。
  3. 風控參數的預設值偏保守,而且那些是佔位值不是建議值 ——
     正式使用前必須由使用者依實際帳戶規模決定。

常數 vs 函式的規則:
  - **機密與存取控制**(資料庫密碼、Dashboard 金鑰、Telegram token、授權名單)
    用**函式**,每次呼叫都重讀環境變數。這樣輪替金鑰不需要重啟服務,
    測試也能改變環境變數而不必處理模組快取。
  - 其他設定用模組常數,啟動時讀一次即可。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"


# ---------------- 讀取小工具 ----------------

def env(*names, default=""):
    """依序尋找多個環境變數名稱,回傳第一個有值的。用來同時支援新舊命名。"""
    for name in names:
        value = os.getenv(name)
        if value not in (None, ""):
            return value.strip()
    return default


def env_float(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def env_int(name, default):
    try:
        return int(float(os.getenv(name, default)))
    except (TypeError, ValueError):
        return int(default)


def env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    raw = os.getenv(name, default) or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------- 應用 ----------------

APP_NAME = "AGMCIS"
VERSION = "1.1.0"
APP_ENV = env("APP_ENV", default="dev")


# ---------------- 資料庫 ----------------

DB_NAME = env("DB_NAME", default="agmcis_db")
DB_USER = env("DB_USER", default="postgres")
DB_HOST = env("DB_HOST", default="localhost")
DB_PORT = env_int("DB_PORT", 5432)


def db_password():
    """機密 —— 每次呼叫重讀環境變數,輪替不需要重啟。"""
    return env("DB_PASSWORD")


def db_config():
    return {
        "dbname": DB_NAME,
        "user": DB_USER,
        "password": db_password(),
        "host": DB_HOST,
        "port": DB_PORT,
    }


# ---------------- 交易所(僅 BingX) ----------------

EXCHANGE = "bingx"

EXCHANGE_CREDENTIALS = {
    "apiKey": env("BINGX_API_KEY"),
    "secret": env("BINGX_API_SECRET"),
}

# 市場型態固定為合約(USDT-M 永續 Swap)。本系統不處理現貨。
MARKET_TYPE = "swap"

# 沒有 API Key 時仍以公開行情端點運作(看盤 / 訊號不需要 Key)
ALLOW_PUBLIC_DATA_ONLY = env_bool("ALLOW_PUBLIC_DATA_ONLY", True)

EXCHANGE_TIMEOUT_MS = env_int("EXCHANGE_TIMEOUT_MS", 8000)
EXCHANGE_MAX_RETRIES = env_int("EXCHANGE_MAX_RETRIES", 3)
EXCHANGE_RETRY_BACKOFF_SECONDS = env_float("EXCHANGE_RETRY_BACKOFF_SECONDS", 0.8)

# 行程內的請求節流。多個 process 共用同一把 API Key 時各自有額度,
# 真正的跨行程限流要等 Phase 16 有共用狀態。
EXCHANGE_RATE_LIMIT_CALLS = env_int("EXCHANGE_RATE_LIMIT_CALLS", 100)

# 跨行程限流(Phase 16)。scheduler / web / telegram 共用同一把 API Key 時,
# 行程內限流會讓實際請求量變成設定值的倍數。
#
# 預設關閉:它需要 migration 006 建好的資料表。**開之前先跑 migration** ——
# 表不存在時每次呼叫都會失敗一次再降級,那比不開更慢。
EXCHANGE_SHARED_RATE_LIMIT = env_bool("EXCHANGE_SHARED_RATE_LIMIT", False)
EXCHANGE_RATE_LIMIT_PERIOD = env_float("EXCHANGE_RATE_LIMIT_PERIOD", 10.0)

# 本機時鐘與交易所允許的最大偏差。超過就會開始被拒簽章,
# 而錯誤訊息看起來像 API Key 有問題,很容易誤判。
EXCHANGE_MAX_CLOCK_SKEW_MS = env_int("EXCHANGE_MAX_CLOCK_SKEW_MS", 3000)

# 使用 BingX 測試環境(VST)。Phase 11 驗證用;正式環境必須是 false。
EXCHANGE_USE_TESTNET = env_bool("EXCHANGE_USE_TESTNET", False)


def has_exchange_credentials():
    return bool(EXCHANGE_CREDENTIALS["apiKey"] and EXCHANGE_CREDENTIALS["secret"])


# ---------------- 監控清單 ----------------

WATCHLIST_SYMBOLS = env_list(
    "WATCHLIST_SYMBOLS", "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT"
)


# ---------------- 快取 ----------------

CACHE_TTL = {
    "ticker": env_float("CACHE_TTL_TICKER", 3),
    "ohlcv": env_float("CACHE_TTL_OHLCV", 15),
    "funding_rate": env_float("CACHE_TTL_FUNDING", 60),
    "open_interest": env_float("CACHE_TTL_OPEN_INTEREST", 30),
}


# ---------------- Paper Trading ----------------

PAPER_START_BALANCE = env_float("PAPER_START_BALANCE", 10000)
DEFAULT_POSITION_SIZE_USDT = env_float("DEFAULT_POSITION_SIZE_USDT", 1000)

# ---- 模擬盤成本(Phase 10)----
#
# 模擬盤與回測**共用同一套成本模型**(agmcis/backtest/costs.py)。
# 兩邊用不同的假設,模擬績效就沒辦法拿來驗證回測 —— 而那正是模擬盤的用途。
#
# 預設值取 BingX 公開費率的保守側。實際費率依 VIP 等級與掛單/吃單而異,
# 要精確就把這些環境變數設成自己帳戶的實際值。
PAPER_MAKER_FEE = env_float("PAPER_MAKER_FEE", 0.0002)
PAPER_TAKER_FEE = env_float("PAPER_TAKER_FEE", 0.0005)
PAPER_SLIPPAGE_PCT = env_float("PAPER_SLIPPAGE_PCT", 0.0005)
PAPER_SPREAD_PCT = env_float("PAPER_SPREAD_PCT", 0.0002)
PAPER_FUNDING_RATE_8H = env_float("PAPER_FUNDING_RATE_8H", 0.0001)
PAPER_LIQUIDATION_FEE = env_float("PAPER_LIQUIDATION_FEE", 0.005)

# 強平時保證金大約剩多少比例。與回測引擎同一個保守值。
PAPER_MAINTENANCE_MARGIN_RATIO = env_float("PAPER_MAINTENANCE_MARGIN_RATIO", 0.10)


# ---------------- 交易模式 ----------------

# 預設 paper。LIVE 必須通過 Phase 17 的 Safety Gate 才可能啟用。
TRADING_MODE = env("TRADING_MODE", default="paper").lower()
AUTO_TRADING_ENABLED = env_bool("AUTO_TRADING", True)


# ---------------- 風控 ----------------
# ⚠️ 以下預設值是保守的佔位值,不是建議值。

MAX_RISK_PER_TRADE_PCT = env_float("MAX_RISK_PER_TRADE_PCT", 1.0)
MAX_DRAWDOWN_PCT = env_float("MAX_DRAWDOWN_PCT", 15)
MAX_EXPOSURE_PCT = env_float("MAX_EXPOSURE_PCT", 80)
MAX_OPEN_POSITIONS = env_int("MAX_OPEN_POSITIONS", 5)
MAX_AUTO_POSITIONS = env_int("MAX_AUTO_POSITIONS", 3)
MAX_LEVERAGE = env_float("MAX_LEVERAGE", 5)
MAX_DAILY_LOSS_USDT = env_float("MAX_DAILY_LOSS_USDT", 300)
MAX_TOTAL_OPEN_LOSS_USDT = env_float("MAX_TOTAL_OPEN_LOSS_USDT", -300)
MAX_CONSECUTIVE_LOSSES = env_int("MAX_CONSECUTIVE_LOSSES", 4)
MAX_TRADES_PER_DAY = env_int("MAX_TRADES_PER_DAY", 10)
MIN_PROFIT_FACTOR = env_float("MIN_PROFIT_FACTOR", 0.8)

MIN_SIGNAL_SCORE = env_float("MIN_SIGNAL_SCORE", 70)
MIN_CONFIDENCE = env_float("MIN_CONFIDENCE", 60)

RISK_ALERT_DISTANCE_PCT = env_float("RISK_ALERT_DISTANCE_PCT", 3)
RISK_EMERGENCY_ROI_PCT = env_float("RISK_EMERGENCY_ROI_PCT", -15)

EMERGENCY_STOP_FILE = env("EMERGENCY_STOP_FILE", default="emergency.stop")
TRADING_PAUSE_FILE = env("TRADING_PAUSE_FILE", default="trading_pause.flag")


def risk_limits_dict():
    return {
        "max_risk_per_trade_pct": MAX_RISK_PER_TRADE_PCT,
        "max_drawdown_pct": MAX_DRAWDOWN_PCT,
        "max_exposure_pct": MAX_EXPOSURE_PCT,
        "max_open_positions": MAX_OPEN_POSITIONS,
        "max_auto_positions": MAX_AUTO_POSITIONS,
        "max_leverage": MAX_LEVERAGE,
        "max_daily_loss_usdt": MAX_DAILY_LOSS_USDT,
        "max_total_open_loss_usdt": MAX_TOTAL_OPEN_LOSS_USDT,
        "max_consecutive_losses": MAX_CONSECUTIVE_LOSSES,
        "max_trades_per_day": MAX_TRADES_PER_DAY,
        "min_profit_factor": MIN_PROFIT_FACTOR,
    }


# ---------------- 排程 ----------------

SCHEDULER_POSITION_INTERVAL = env_int("SCHEDULER_POSITION_INTERVAL", 60)
SCHEDULER_TRADER_INTERVAL = env_int("SCHEDULER_TRADER_INTERVAL", 300)
SCHEDULER_OPPORTUNITY_INTERVAL = env_int("SCHEDULER_OPPORTUNITY_INTERVAL", 1800)
SCHEDULER_TRAILING_INTERVAL = env_int("SCHEDULER_TRAILING_INTERVAL", 120)

# 判斷式出場(Agent 群認為該收了)。比停損檢查慢是刻意的 ——
# 停損是機械式的、必須快;判斷式出場不該每分鐘重新想一次。
SCHEDULER_EXIT_MANAGER_INTERVAL = env_int("SCHEDULER_EXIT_MANAGER_INTERVAL", 300)

# 裸倉巡檢。沒有停損的部位沒有虧損上限,這是安全網 ——
# 主要防線是 Execution Engine 開倉時的檢查。
SCHEDULER_NAKED_SWEEP_INTERVAL = env_int("SCHEDULER_NAKED_SWEEP_INTERVAL", 120)

# 對帳。狀態不明的訂單不可以重送,只能查 —— 查得越快越好,
# 但查詢本身要打交易所 API,所以不宜每分鐘跑。
SCHEDULER_RECONCILE_INTERVAL = env_int("SCHEDULER_RECONCILE_INTERVAL", 180)
SCHEDULER_RISK_ALERT_INTERVAL = env_int("SCHEDULER_RISK_ALERT_INTERVAL", 300)
SCHEDULER_TICK_SECONDS = env_int("SCHEDULER_TICK_SECONDS", 10)
SCHEDULER_STATUS_FILE = env("SCHEDULER_STATUS_FILE", default="scheduler_status.json")
DAILY_REPORT_HOUR = env_int("DAILY_REPORT_HOUR", 8)

WEBSOCKET_INTERVAL = env_int("WEBSOCKET_INTERVAL", 5)


# ---------------- Telegram ----------------

def telegram_bot_token():
    """機密。同時支援舊名 BOT_TOKEN。"""
    return env("TELEGRAM_BOT_TOKEN", "BOT_TOKEN")


def telegram_chat_id():
    """同時支援舊名 CHAT_ID。"""
    return env("TELEGRAM_CHAT_ID", "CHAT_ID")


def telegram_api_base():
    token = telegram_bot_token()
    return f"https://api.telegram.org/bot{token}" if token else ""


def disable_telegram_push():
    return env_bool("DISABLE_TELEGRAM_PUSH", False)


def telegram_allowed_chat_ids():
    """
    可以下指令的 chat id 集合。

    空集合代表「沒有人能下指令」—— 沒有設定授權名單時拒絕所有人,
    比「沒設定就全部放行」安全得多。
    """
    allowed = set(env_list("TELEGRAM_ALLOWED_CHAT_IDS"))
    chat_id = telegram_chat_id()
    if not allowed and chat_id:
        allowed.add(str(chat_id))
    return allowed


def telegram_is_configured():
    return bool(telegram_bot_token() and telegram_chat_id())


def telegram_is_authorized(chat_id):
    """白名單之外一律拒絕;白名單為空時拒絕所有人。"""
    if chat_id is None:
        return False
    return str(chat_id) in telegram_allowed_chat_ids()


# ---------------- Web / API 存取 ----------------

def dashboard_key():
    """機密。未設定時回傳空字串,而空金鑰代表「拒絕所有人」而不是「放行所有人」。"""
    return env("DASHBOARD_KEY")


SESSION_COOKIE_NAME = "agmcis_session"
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", False)
SESSION_COOKIE_MAX_AGE = env_int("SESSION_COOKIE_MAX_AGE", 7 * 24 * 3600)
API_KEY_HEADER = "X-AGMCIS-KEY"


# ---------------- 備份 ----------------

BACKUP_DIR = env("BACKUP_DIR", default="backups")
BACKUP_KEEP_LAST = env_int("BACKUP_KEEP_LAST", 30)


def as_dict(include_secrets=False):
    """
    可觀測性用的組態快照。
    預設遮蔽所有機密 —— 這個結果可能會出現在 log、健康檢查或 Dashboard 上。
    """
    snapshot = {
        "app_name": APP_NAME,
        "version": VERSION,
        "app_env": APP_ENV,
        "exchange": EXCHANGE,
        "market_type": MARKET_TYPE,
        "trading_mode": TRADING_MODE,
        "auto_trading_enabled": AUTO_TRADING_ENABLED,
        "watchlist_symbols": WATCHLIST_SYMBOLS,
        "paper_start_balance": PAPER_START_BALANCE,
        "risk_limits": risk_limits_dict(),
        "db_host": DB_HOST,
        "db_name": DB_NAME,
        "has_exchange_credentials": has_exchange_credentials(),
        "telegram_configured": telegram_is_configured(),
        "telegram_allowed_chats": len(telegram_allowed_chat_ids()),
        "dashboard_key_set": bool(dashboard_key()),
    }

    if include_secrets:
        snapshot["db_password"] = db_password()
        snapshot["dashboard_key"] = dashboard_key()
        snapshot["telegram_bot_token"] = telegram_bot_token()
        snapshot["exchange_credentials"] = EXCHANGE_CREDENTIALS

    return snapshot
