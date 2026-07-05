"""
Exchange Engine — BingX 合約專用

對外函式介面跟原版完全相容(get_exchange / test_connection /
fetch_ticker_safe / get_price_safe / fetch_ohlcv_safe),
呼叫端(market_data.py、api/system_health.py)完全不用改。
內部行為改成:只用 BingX、鎖定合約(USDT-M 永續 Swap)、加上重試機制。
"""
import os
import time
import logging

import ccxt

logger = logging.getLogger("agmcis.exchange_engine")

MAX_RETRIES = int(os.getenv("EXCHANGE_MAX_RETRIES", "3"))
RETRY_BACKOFF_SECONDS = float(os.getenv("EXCHANGE_RETRY_BACKOFF_SECONDS", "0.8"))

EXCHANGES = {
    "bingx": ccxt.bingx({
        "apiKey": os.getenv("BINGX_API_KEY", ""),
        "secret": os.getenv("BINGX_API_SECRET", ""),
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    })
}


def to_contract_symbol(symbol: str) -> str:
    symbol = symbol.upper()
    if ":" in symbol:
        return symbol
    quote = symbol.split("/")[-1]
    return f"{symbol}:{quote}"


def get_exchange(name):
    return EXCHANGES.get(name)


def _call_with_retry(exchange, fn_name, *args, **kwargs):
    fn = getattr(exchange, fn_name)
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout) as e:
            last_error = e
            logger.warning(
                "[bingx] %s attempt %d/%d failed (retryable): %s",
                fn_name, attempt, MAX_RETRIES, e,
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
        except ccxt.BaseError as e:
            last_error = e
            logger.error("[bingx] %s non-retryable error: %s", fn_name, e)
            break
    raise last_error


def test_connection():
    result = {}
    for name, exchange in EXCHANGES.items():
        try:
            ticker = _call_with_retry(exchange, "fetch_ticker", to_contract_symbol("BTC/USDT"))
            result[name] = {"success": True, "last": ticker["last"]}
        except Exception as e:
            result[name] = {"success": False, "error": str(e)}
    return result


def fetch_ticker_safe(symbol):
    exchange = EXCHANGES["bingx"]
    try:
        ticker = _call_with_retry(exchange, "fetch_ticker", to_contract_symbol(symbol))
        return {"exchange": "bingx", "ticker": ticker}
    except Exception as e:
        logger.error("fetch_ticker_safe failed for %s: %s", symbol, e)
        return None


def get_price_safe(symbol):
    result = fetch_ticker_safe(symbol)
    if not result:
        return None
    ticker = result.get("ticker", {})
    return {
        "symbol": symbol,
        "price": ticker.get("last"),
        "exchange": result.get("exchange"),
    }


def fetch_ohlcv_safe(symbol, timeframe="1h", limit=200):
    exchange = EXCHANGES["bingx"]
    try:
        data = _call_with_retry(
            exchange, "fetch_ohlcv", to_contract_symbol(symbol), timeframe, None, limit
        )
        return {"exchange": "bingx", "ohlcv": data}
    except Exception as e:
        logger.error("fetch_ohlcv_safe failed for %s: %s", symbol, e)
        return None
