"""
Exchange Engine — BingX 合約專用

設計原則:
  1. 專注單一交易所(BingX),不做多交易所備援 —— 少一層複雜度,少一種「兩邊資料不一致」的風險。
  2. 只處理合約(USDT-M 永續 Swap),不管現貨。
  3. 重試分兩種:
     - 網路/逾時類錯誤 -> 值得重試(指數退避)
     - 交易所明確拒絕(symbol 不存在、權限不足)-> 重試沒用,直接放棄並清楚拋出
  4. 呼叫端只需要處理一種例外:ExchangeUnavailableError。

需要安裝:pip install ccxt
需要設定:.env 內的 BINGX_API_KEY / BINGX_API_SECRET(下單才需要;純看行情/訊號不需要)
"""
import time
import logging
from typing import Any, Callable, Dict, List, Optional

import ccxt

from config import (
    EXCHANGE,
    EXCHANGE_CREDENTIALS,
    MARKET_TYPE,
    EXCHANGE_TIMEOUT_MS,
    EXCHANGE_MAX_RETRIES,
    EXCHANGE_RETRY_BACKOFF_SECONDS,
)

logger = logging.getLogger("agmcis.exchange_engine")


class ExchangeUnavailableError(Exception):
    """重試用盡仍失敗時拋出,攜帶原始錯誤方便除錯。"""


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

        raise ExchangeUnavailableError(f"{fn_name} failed on bingx: {last_error!r}") from last_error

    # ---------------- 對外方法(回傳格式固定) ----------------

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

    def get_ohlcv(self, symbol: str, timeframe: str = "15m", limit: int = 120) -> List[Dict]:
        raw = self._call("fetch_ohlcv", symbol, timeframe, None, limit)
        return [
            {"time": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]}
            for r in raw
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

    # ---------------- 合約專屬方法(現貨沒有這些) ----------------

    def get_positions(self, symbols: Optional[List[str]] = None) -> List[Dict]:
        """取得目前持有的合約部位(需要 API Key)。"""
        raw = self._call("fetch_positions", symbols)
        return raw or []

    def get_balance(self) -> Dict:
        """取得合約帳戶餘額(需要 API Key)。"""
        return self._call("fetch_balance")

    def create_order(self, symbol: str, side: str, amount: float,
                      order_type: str = "market", price: Optional[float] = None,
                      params: Optional[Dict] = None) -> Dict:
        """
        下合約單。side: 'buy' | 'sell'。
        目前僅封裝介面,尚未接上風控檢查 —— 風控層完成前不要直接呼叫這個方法下真單。
        """
        return self._call("create_order", symbol, order_type, side, amount, price, params or {})


# 全域單例,供 market_data.py 及其他服務共用
engine = ExchangeEngine()
