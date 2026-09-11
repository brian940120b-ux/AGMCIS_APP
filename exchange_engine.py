"""
Exchange Engine — BingX 合約專用。

設計原則:
  1. 專注單一交易所(BingX),不做多交易所備援 —— 少一層複雜度,
     也少一種「兩邊資料不一致」的風險。
  2. 只處理合約(USDT-M 永續 Swap)。
  3. 重試分兩種:
     - 網路 / 逾時類錯誤 -> 值得重試(指數退避)
     - 交易所明確拒絕(symbol 不存在、權限不足)-> 重試沒用,直接放棄
  4. 呼叫端只需要處理一種例外:ExchangeUnavailableError。
  5. 交易所實體透過 exchange_factory 注入,不在 import 時就建立 ——
     這是能對這一層寫單元測試的前提。

Phase 0.5 說明:
  class 版的 ExchangeEngine 曾經寫好又被回退(留下 exchange_engine.py.broken_backup),
  但它的測試 tests/test_exchange_engine.py 留了下來,所以測試一直是紅燈。
  這次把 class 復原,同時保留原本的模組層函式(fetch_ticker_safe / get_price_safe /
  fetch_ohlcv_safe / test_connection / get_exchange),讓 market_data.py 與
  api/system_health.py 等既有呼叫端完全不用改 —— 當初會「broken」就是因為
  回傳格式換掉而呼叫端沒跟上。
"""
import logging
import time
from typing import Any, Callable, Dict, List, Optional

import ccxt

from config import (
    EXCHANGE,
    EXCHANGE_CREDENTIALS,
    EXCHANGE_MAX_RETRIES,
    EXCHANGE_RETRY_BACKOFF_SECONDS,
    EXCHANGE_TIMEOUT_MS,
    MARKET_TYPE,
)

logger = logging.getLogger("agmcis.exchange_engine")


class ExchangeUnavailableError(Exception):
    """重試用盡仍失敗時拋出,攜帶原始錯誤方便除錯。"""


def to_contract_symbol(symbol: str) -> str:
    """'BTC/USDT' -> 'BTC/USDT:USDT'(ccxt 統一格式下的 USDT-M 永續合約符號)"""
    symbol = symbol.upper()
    if ":" in symbol:
        return symbol
    quote = symbol.split("/")[-1]
    return f"{symbol}:{quote}"


def _build_exchange():
    klass = getattr(ccxt, EXCHANGE)
    return klass({
        **{k: v for k, v in EXCHANGE_CREDENTIALS.items() if v},
        "enableRateLimit": True,
        "timeout": EXCHANGE_TIMEOUT_MS,
        "options": {"defaultType": MARKET_TYPE},
    })


class ExchangeEngine:
    def __init__(self, exchange_factory: Callable[[], Any] = _build_exchange):
        self._factory = exchange_factory
        self._instance: Optional[Any] = None

    def _get_instance(self):
        if self._instance is None:
            self._instance = self._factory()
        return self._instance

    def _call(self, fn_name: str, *args, **kwargs):
        exchange = self._get_instance()
        fn = getattr(exchange, fn_name)
        last_error = None

        for attempt in range(1, EXCHANGE_MAX_RETRIES + 1):
            try:
                return fn(*args, **kwargs)
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout) as e:
                last_error = e
                logger.warning(
                    "[bingx] %s attempt %d/%d failed (retryable): %s",
                    fn_name, attempt, EXCHANGE_MAX_RETRIES, e,
                )
                if attempt < EXCHANGE_MAX_RETRIES:
                    time.sleep(EXCHANGE_RETRY_BACKOFF_SECONDS * attempt)
            except ccxt.BaseError as e:
                last_error = e
                logger.error("[bingx] %s non-retryable error: %s", fn_name, e)
                break

        raise ExchangeUnavailableError(
            f"{fn_name} failed on bingx: {last_error!r}"
        ) from last_error

    # ---------------- 行情 ----------------

    def get_ticker(self, symbol: str) -> Dict:
        raw = self._call("fetch_ticker", symbol)
        return {
            "symbol": symbol,
            "price": raw.get("last"),
            "bid": raw.get("bid"),
            "ask": raw.get("ask"),
            "high_24h": raw.get("high"),
            "low_24h": raw.get("low"),
            "change_pct_24h": raw.get("percentage"),
            "volume_24h": raw.get("baseVolume"),
            "timestamp": raw.get("timestamp"),
        }

    def get_ohlcv_raw(self, symbol: str, timeframe: str = "15m", limit: int = 120):
        """回傳 ccxt 原始的 [[ts, o, h, l, c, v], ...],給需要餵進 pandas 的呼叫端。"""
        return self._call("fetch_ohlcv", symbol, timeframe, None, limit)

    def get_ohlcv(self, symbol: str, timeframe: str = "15m", limit: int = 120) -> List[Dict]:
        return [
            {"time": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]}
            for r in self.get_ohlcv_raw(symbol, timeframe, limit)
        ]

    def get_funding_rate(self, symbol: str) -> Optional[Dict]:
        try:
            raw = self._call("fetch_funding_rate", symbol)
        except ExchangeUnavailableError:
            logger.warning("funding_rate unavailable for %s, returning None", symbol)
            return None
        if not raw:
            return None
        return {
            "symbol": symbol,
            "funding_rate": raw.get("fundingRate"),
            "next_funding_time": raw.get("fundingTimestamp"),
        }

    def get_open_interest(self, symbol: str) -> Optional[Dict]:
        try:
            raw = self._call("fetch_open_interest", symbol)
        except ExchangeUnavailableError:
            logger.warning("open_interest unavailable for %s, returning None", symbol)
            return None
        if not raw:
            return None
        return {
            "symbol": symbol,
            "open_interest": raw.get("openInterestAmount") or raw.get("openInterestValue"),
            "timestamp": raw.get("timestamp"),
        }

    # ---------------- 合約專屬(需要 API Key) ----------------

    def get_positions(self, symbols: Optional[List[str]] = None) -> List[Dict]:
        return self._call("fetch_positions", symbols) or []

    def get_balance(self) -> Dict:
        return self._call("fetch_balance")

    def create_order(self, symbol: str, side: str, amount: float,
                     order_type: str = "market", price: Optional[float] = None,
                     params: Optional[Dict] = None) -> Dict:
        """
        下合約單。side: 'buy' | 'sell'。

        ⚠️ 目前僅封裝介面。Trading Rules Engine(Phase 4)與 Execution Engine(Phase 12)
        完成之前,不要直接用這個方法下真單 —— 它沒有精度驗證、沒有風控、沒有狀態機。
        """
        return self._call(
            "create_order", symbol, order_type, side, amount, price, params or {}
        )


# 全域單例,供 market_data.py 及其他服務共用
engine = ExchangeEngine()


# ---------------- 向下相容的模組層介面 ----------------
# 既有呼叫端(market_data.py / api/system_health.py)使用這些函式與回傳格式。
# 行為與 Phase 0.5 之前完全一致:失敗回 None,不拋例外。

EXCHANGES = {"bingx": engine}


def get_exchange(name):
    return EXCHANGES.get(name)


def test_connection():
    result = {}
    try:
        ticker = engine.get_ticker(to_contract_symbol("BTC/USDT"))
        result["bingx"] = {"success": True, "last": ticker["price"]}
    except Exception as e:
        result["bingx"] = {"success": False, "error": str(e)}
    return result


def fetch_ticker_safe(symbol):
    try:
        return {"exchange": "bingx", "ticker": engine.get_ticker(to_contract_symbol(symbol))}
    except ExchangeUnavailableError as e:
        logger.error("fetch_ticker_safe failed for %s: %s", symbol, e)
        return None


def get_price_safe(symbol):
    result = fetch_ticker_safe(symbol)
    if not result:
        return None
    ticker = result.get("ticker", {})
    return {"symbol": symbol, "price": ticker.get("price"), "exchange": "bingx"}


def fetch_ohlcv_safe(symbol, timeframe="1h", limit=200):
    try:
        data = engine.get_ohlcv_raw(to_contract_symbol(symbol), timeframe, limit)
        return {"exchange": "bingx", "ohlcv": data}
    except ExchangeUnavailableError as e:
        logger.error("fetch_ohlcv_safe failed for %s: %s", symbol, e)
        return None
