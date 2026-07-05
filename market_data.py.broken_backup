"""
Market Data Service — BingX 合約專用

給 indicators / scanner_service / dashboard 用的對外介面。
底層打 API 的邏輯都在 exchange_engine.py,這裡只負責:
  1. symbol 格式轉換(統一轉成合約格式)
  2. 套用快取(避免同一秒被 scanner 掃多個幣時對交易所連發請求)
"""
import logging
from typing import Dict, List, Optional

from config import CACHE_TTL
from cache_service import cache
from exchange_engine import engine, ExchangeUnavailableError

logger = logging.getLogger("agmcis.market_data")


def to_market_symbol(base_symbol: str) -> str:
    """'BTC/USDT' -> 'BTC/USDT:USDT'(ccxt 統一格式下的 USDT-M 永續合約符號)"""
    base_symbol = base_symbol.upper()
    if ":" in base_symbol:
        return base_symbol
    quote = base_symbol.split("/")[-1]
    return f"{base_symbol}:{quote}"


def get_price(symbol: str) -> Optional[float]:
    ticker = get_ticker(symbol)
    return ticker["price"] if ticker else None


def get_ticker(symbol: str) -> Optional[Dict]:
    market_symbol = to_market_symbol(symbol)
    key = f"ticker:{market_symbol}"
    try:
        return cache.get_or_fetch(
            key, CACHE_TTL["ticker"],
            lambda: engine.get_ticker(market_symbol),
        )
    except ExchangeUnavailableError as e:
        logger.error("get_ticker failed for %s: %s", symbol, e)
        return None


def get_ohlcv(symbol: str, timeframe: str = "15m", limit: int = 120) -> List[Dict]:
    market_symbol = to_market_symbol(symbol)
    key = f"ohlcv:{market_symbol}:{timeframe}:{limit}"
    try:
        return cache.get_or_fetch(
            key, CACHE_TTL["ohlcv"],
            lambda: engine.get_ohlcv(market_symbol, timeframe, limit),
        )
    except ExchangeUnavailableError as e:
        logger.error("get_ohlcv failed for %s: %s", symbol, e)
        return []


def get_funding_rate(symbol: str) -> Optional[Dict]:
    market_symbol = to_market_symbol(symbol)
    key = f"funding:{market_symbol}"
    return cache.get_or_fetch(
        key, CACHE_TTL["funding_rate"],
        lambda: engine.get_funding_rate(market_symbol),
    )


def get_open_interest(symbol: str) -> Optional[Dict]:
    market_symbol = to_market_symbol(symbol)
    key = f"oi:{market_symbol}"
    return cache.get_or_fetch(
        key, CACHE_TTL["open_interest"],
        lambda: engine.get_open_interest(market_symbol),
    )


def get_market_snapshot(symbol: str) -> Dict:
    """
    給訊號層/儀表板用的綜合快照:一次拿到價格、24h變動、資金費率、持倉量。
    任一項目失敗都不讓整體掛掉,缺的欄位就回傳 None,由前端自行顯示「--」。
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
