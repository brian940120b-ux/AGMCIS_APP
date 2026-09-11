"""
Market Data Service — BingX 合約專用。

對外契約(刻意保持與 Phase 0.5 之前完全一致,避免破壞生產):
  - get_price(symbol)                 -> float | None
  - get_ohlcv(symbol, tf, limit)      -> pandas.DataFrame | None
    (technical_service.py 與 strategy.py 直接對它做 df["close"],不能換成 list of dict
     —— 當初的重構就是在這裡 break 掉才被回退的)

Phase 0.5 新增的能力(供 Phase 2/3 的訊號層使用):
  - get_ticker / get_funding_rate / get_open_interest / get_market_snapshot

快取統一改用 cache_service。原本這個檔案自己維護兩份 ad-hoc dict 快取,
而設計良好的 cache_service 完全沒有人使用。
"""
import logging
from typing import Dict, List, Optional

import pandas as pd

from cache_service import cache
from config import CACHE_TTL
from exchange_engine import (
    ExchangeUnavailableError,
    engine,
    to_contract_symbol,
)

logger = logging.getLogger("agmcis.market_data")

OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def to_market_symbol(base_symbol: str) -> str:
    return to_contract_symbol(base_symbol)


def get_exchange_for_symbol(symbol):
    """向下相容:舊呼叫端期待 (name, exchange) 的 tuple。"""
    return ("bingx", engine)


# ---------------- Ticker ----------------

def get_ticker(symbol: str) -> Optional[Dict]:
    market_symbol = to_market_symbol(symbol)
    try:
        return cache.get_or_fetch(
            f"ticker:{market_symbol}",
            CACHE_TTL["ticker"],
            lambda: engine.get_ticker(market_symbol),
        )
    except ExchangeUnavailableError as e:
        logger.error("get_ticker failed for %s: %s", symbol, e)
        return None


def get_price(symbol: str) -> Optional[float]:
    ticker = get_ticker(symbol)
    if not ticker:
        return None
    price = ticker.get("price")
    return float(price) if price is not None else None


# ---------------- K 線 ----------------

def get_ohlcv(symbol: str = "BTC/USDT", timeframe: str = "1h", limit: int = 150):
    """
    回傳 pandas DataFrame(columns: timestamp/open/high/low/close/volume + exchange)。
    取得失敗時回傳 None —— 呼叫端(technical_service)已經會處理 None 並標記 data_ok=False。
    """
    market_symbol = to_market_symbol(symbol)

    try:
        rows = cache.get_or_fetch(
            f"ohlcv:{market_symbol}:{timeframe}:{limit}",
            CACHE_TTL["ohlcv"],
            lambda: engine.get_ohlcv_raw(market_symbol, timeframe, limit),
        )
    except ExchangeUnavailableError as e:
        logger.error("get_ohlcv failed for %s %s: %s", symbol, timeframe, e)
        return None

    if not rows:
        logger.warning("get_ohlcv returned empty for %s %s", symbol, timeframe)
        return None

    try:
        df = pd.DataFrame(rows, columns=OHLCV_COLUMNS)
        df["exchange"] = "bingx"
        return df
    except Exception as e:
        logger.exception("get_ohlcv dataframe build failed for %s: %s", symbol, e)
        return None


def get_ohlcv_dicts(symbol: str, timeframe: str = "15m", limit: int = 120) -> List[Dict]:
    """給不需要 pandas 的呼叫端(例如未來的 API 回傳)使用。"""
    df = get_ohlcv(symbol, timeframe, limit)
    if df is None:
        return []
    return df[OHLCV_COLUMNS].to_dict("records")


# ---------------- 衍生品資料(合約特有) ----------------

def get_funding_rate(symbol: str) -> Optional[Dict]:
    market_symbol = to_market_symbol(symbol)
    return cache.get_or_fetch(
        f"funding:{market_symbol}",
        CACHE_TTL["funding_rate"],
        lambda: engine.get_funding_rate(market_symbol),
    )


def get_open_interest(symbol: str) -> Optional[Dict]:
    market_symbol = to_market_symbol(symbol)
    return cache.get_or_fetch(
        f"oi:{market_symbol}",
        CACHE_TTL["open_interest"],
        lambda: engine.get_open_interest(market_symbol),
    )


def get_market_snapshot(symbol: str) -> Dict:
    """
    給訊號層 / 儀表板用的綜合快照。
    任一項目失敗都不讓整體掛掉,缺的欄位回傳 None,由前端顯示「--」。
    """
    ticker = get_ticker(symbol) or {}
    funding = get_funding_rate(symbol)
    oi = get_open_interest(symbol)

    return {
        "symbol": symbol,
        "price": ticker.get("price"),
        "change_pct_24h": ticker.get("change_pct_24h"),
        "high_24h": ticker.get("high_24h"),
        "low_24h": ticker.get("low_24h"),
        "volume_24h": ticker.get("volume_24h"),
        "funding_rate": funding.get("funding_rate") if funding else None,
        "open_interest": oi.get("open_interest") if oi else None,
    }
