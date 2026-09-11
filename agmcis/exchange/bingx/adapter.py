"""
BingX ExchangeAdapter 實作。

Phase 2 範圍:行情 + 合約規則 + 衍生品資料。
下單與帳戶方法的介面已就位,但風控與 Trading Rules Engine 完成前不可直接使用。

設計:
  - ccxt 實體透過 exchange_factory 注入,不在 import 時建立 —— 這是能寫單元測試的前提。
  - 重試分兩種:網路/逾時值得重試(指數退避);交易所明確拒絕不值得重試。
  - 核心行情失敗拋例外,輔助資料失敗回 None(見 base.py 的約定)。
  - Standard 與 Perpetual 用不同的 defaultType 與符號格式。
"""
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import ccxt

from agmcis.config import settings
from agmcis.core.enums import MarketType, OrderSide, OrderType
from agmcis.core.errors import ExchangeUnavailableError
from agmcis.core.models import TradingRules
from agmcis.exchange.base import ExchangeAdapter

logger = logging.getLogger("agmcis.exchange.bingx")

# ccxt 的 defaultType。BingX 的永續是 swap;標準合約走 futures。
_CCXT_MARKET_TYPE = {
    MarketType.PERPETUAL: "swap",
    MarketType.STANDARD: "futures",
}

CAPABILITIES = frozenset({
    "ticker", "ohlcv", "order_book", "funding_rate", "open_interest",
    "trading_rules", "perpetual_futures",
})


def _build_ccxt_exchange(market_type=MarketType.PERPETUAL):
    credentials = {k: v for k, v in settings.EXCHANGE_CREDENTIALS.items() if v}
    return ccxt.bingx({
        **credentials,
        "enableRateLimit": True,
        "timeout": settings.EXCHANGE_TIMEOUT_MS,
        "options": {"defaultType": _CCXT_MARKET_TYPE[market_type]},
    })


class BingXAdapter(ExchangeAdapter):
    name = "bingx"

    def __init__(self, exchange_factory=_build_ccxt_exchange,
                 max_retries=None, backoff_seconds=None):
        self._factory = exchange_factory
        self._instances = {}
        self._markets_cache = {}
        self.max_retries = max_retries or settings.EXCHANGE_MAX_RETRIES
        self.backoff_seconds = (
            backoff_seconds if backoff_seconds is not None
            else settings.EXCHANGE_RETRY_BACKOFF_SECONDS
        )

    def capabilities(self):
        # standard_futures 刻意不在清單裡:符號與規則的處理已就位,
        # 但尚未對真實 BingX Standard 市場驗證過(Phase 3)。
        return CAPABILITIES

    # ---------------- 底層呼叫 ----------------

    def _instance(self, market_type):
        market_type = MarketType.parse(market_type, MarketType.PERPETUAL)
        if market_type not in self._instances:
            self._instances[market_type] = self._factory(market_type)
        return self._instances[market_type]

    def _call(self, fn_name, *args, market_type=MarketType.PERPETUAL, **kwargs):
        exchange = self._instance(market_type)
        fn = getattr(exchange, fn_name)
        last_error = None

        for attempt in range(1, self.max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout) as exc:
                last_error = exc
                logger.warning(
                    "[bingx] %s attempt %d/%d failed (retryable): %s",
                    fn_name, attempt, self.max_retries, exc,
                )
                if attempt < self.max_retries:
                    time.sleep(self.backoff_seconds * attempt)
            except ccxt.BaseError as exc:
                # 交易所明確拒絕(symbol 不存在、權限不足)重試沒用,直接放棄
                last_error = exc
                logger.error("[bingx] %s non-retryable error: %s", fn_name, exc)
                break

        raise ExchangeUnavailableError(
            f"{fn_name} failed on bingx: {last_error!r}"
        ) from last_error

    def _optional(self, fn_name, *args, market_type=MarketType.PERPETUAL, **kwargs):
        """輔助資料:失敗回 None 而不是拋例外。"""
        try:
            return self._call(fn_name, *args, market_type=market_type, **kwargs)
        except ExchangeUnavailableError as exc:
            logger.warning("[bingx] %s unavailable: %s", fn_name, exc)
            return None

    # ---------------- 市場資訊 ----------------

    def to_market_symbol(self, symbol, market_type=MarketType.PERPETUAL):
        market_type = MarketType.parse(market_type, MarketType.PERPETUAL)
        symbol = str(symbol).upper().strip()

        if ":" in symbol:
            return symbol

        if market_type is MarketType.PERPETUAL:
            # ccxt 統一格式下的 USDT-M 永續:BTC/USDT:USDT
            quote = symbol.split("/")[-1]
            return f"{symbol}:{quote}"

        # Standard Futures 在 ccxt 不加結算後綴
        return symbol

    def load_markets(self, market_type=MarketType.PERPETUAL, reload=False):
        market_type = MarketType.parse(market_type, MarketType.PERPETUAL)
        if reload or market_type not in self._markets_cache:
            self._markets_cache[market_type] = self._call(
                "load_markets", market_type=market_type
            ) or {}
        return self._markets_cache[market_type]

    def list_markets(self, market_type=MarketType.PERPETUAL):
        market_type = MarketType.parse(market_type, MarketType.PERPETUAL)
        markets = self.load_markets(market_type)

        wanted = _CCXT_MARKET_TYPE[market_type]
        result = []

        for symbol, market in markets.items():
            if not market.get("active", True):
                continue
            if market.get("type") and market["type"] != wanted:
                continue
            if market.get("quote") != "USDT":
                continue

            result.append({
                "symbol": symbol,
                "base": market.get("base"),
                "quote": market.get("quote"),
                "market_type": market_type.value,
                "contract_size": market.get("contractSize"),
            })

        return result

    def get_trading_rules(self, symbol, market_type=MarketType.PERPETUAL):
        market_type = MarketType.parse(market_type, MarketType.PERPETUAL)
        market_symbol = self.to_market_symbol(symbol, market_type)
        markets = self.load_markets(market_type)
        market = markets.get(market_symbol) or markets.get(symbol)

        if not market:
            raise ExchangeUnavailableError(
                f"{symbol} 在 bingx {market_type.value} 找不到合約定義"
            )

        limits = market.get("limits") or {}
        precision = market.get("precision") or {}
        amount_limits = limits.get("amount") or {}
        cost_limits = limits.get("cost") or {}
        leverage_limits = limits.get("leverage") or {}

        return TradingRules(
            exchange=self.name,
            market_type=market_type,
            symbol=market_symbol,
            tick_size=precision.get("price"),
            step_size=precision.get("amount"),
            min_qty=amount_limits.get("min"),
            max_qty=amount_limits.get("max"),
            min_notional=cost_limits.get("min"),
            contract_size=market.get("contractSize"),
            price_precision=_as_int(precision.get("price")),
            qty_precision=_as_int(precision.get("amount")),
            max_leverage=leverage_limits.get("max"),
            fetched_at=datetime.now(timezone.utc),
        )

    # ---------------- 行情 ----------------

    def get_ticker(self, symbol, market_type=MarketType.PERPETUAL):
        market_symbol = self.to_market_symbol(symbol, market_type)
        raw = self._call("fetch_ticker", market_symbol, market_type=market_type)

        return {
            "symbol": symbol,
            "market_symbol": market_symbol,
            "price": raw.get("last"),
            "bid": raw.get("bid"),
            "ask": raw.get("ask"),
            "high_24h": raw.get("high"),
            "low_24h": raw.get("low"),
            "change_pct_24h": raw.get("percentage"),
            "volume_24h": raw.get("baseVolume"),
            "quote_volume_24h": raw.get("quoteVolume"),
            "timestamp": raw.get("timestamp"),
        }

    def get_ohlcv(self, symbol, timeframe="1h", limit=150,
                  market_type=MarketType.PERPETUAL):
        market_symbol = self.to_market_symbol(symbol, market_type)
        return self._call(
            "fetch_ohlcv", market_symbol, timeframe, None, limit,
            market_type=market_type,
        )

    def get_tickers(self, market_type=MarketType.PERPETUAL):
        """一次取得該市場所有合約的 ticker。失敗拋例外。"""
        return self._call("fetch_tickers", market_type=market_type) or {}

    def get_order_book(self, symbol, limit=20, market_type=MarketType.PERPETUAL):
        market_symbol = self.to_market_symbol(symbol, market_type)
        raw = self._optional(
            "fetch_order_book", market_symbol, limit, market_type=market_type
        )
        if not raw:
            return None

        bids = raw.get("bids") or []
        asks = raw.get("asks") or []

        if not bids or not asks:
            return None

        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        bid_volume = sum(float(level[1]) for level in bids)
        ask_volume = sum(float(level[1]) for level in asks)
        total = bid_volume + ask_volume

        mid = (best_bid + best_ask) / 2

        return {
            "symbol": symbol,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": best_ask - best_bid,
            "spread_pct": ((best_ask - best_bid) / mid * 100) if mid > 0 else None,
            "bid_volume": bid_volume,
            "ask_volume": ask_volume,
            # > 0 代表買盤較厚,< 0 代表賣盤較厚。範圍 -1 ~ 1。
            "imbalance": ((bid_volume - ask_volume) / total) if total > 0 else None,
            "timestamp": raw.get("timestamp"),
        }

    # ---------------- 衍生品資料 ----------------

    def get_funding_rate(self, symbol, market_type=MarketType.PERPETUAL):
        market_type = MarketType.parse(market_type, MarketType.PERPETUAL)

        # Standard Futures 沒有資金費率,不該白打一次 API
        if market_type is not MarketType.PERPETUAL:
            return None

        market_symbol = self.to_market_symbol(symbol, market_type)
        raw = self._optional("fetch_funding_rate", market_symbol, market_type=market_type)
        if not raw:
            return None

        return {
            "symbol": symbol,
            "funding_rate": raw.get("fundingRate"),
            "next_funding_time": raw.get("fundingTimestamp") or raw.get("nextFundingTimestamp"),
            "mark_price": raw.get("markPrice"),
            "index_price": raw.get("indexPrice"),
        }

    def get_open_interest(self, symbol, market_type=MarketType.PERPETUAL):
        market_symbol = self.to_market_symbol(symbol, market_type)
        raw = self._optional("fetch_open_interest", market_symbol, market_type=market_type)
        if not raw:
            return None

        return {
            "symbol": symbol,
            "open_interest": raw.get("openInterestAmount") or raw.get("openInterestValue"),
            "open_interest_value": raw.get("openInterestValue"),
            "timestamp": raw.get("timestamp"),
        }

    # ---------------- 帳戶 ----------------

    def get_balance(self):
        return self._call("fetch_balance")

    def get_positions(self, symbols=None):
        return self._call("fetch_positions", symbols) or []

    # ---------------- 下單 ----------------

    def create_order(self, symbol, side, quantity, order_type=OrderType.MARKET,
                     price=None, market_type=MarketType.PERPETUAL,
                     client_order_id=None, reduce_only=False, params=None):
        """
        ⚠️ Trading Rules Engine(Phase 4)與 Execution Engine(Phase 12)完成前
        不可直接呼叫。這裡沒有精度驗證、沒有風控、沒有狀態機、沒有對帳。
        """
        market_symbol = self.to_market_symbol(symbol, market_type)
        side = OrderSide.parse(side, OrderSide.BUY)
        order_type = OrderType.parse(order_type, OrderType.MARKET)

        request = dict(params or {})
        if client_order_id:
            request["clientOrderId"] = client_order_id
        if reduce_only:
            request["reduceOnly"] = True

        return self._call(
            "create_order", market_symbol, order_type.value, side.value,
            quantity, price, request, market_type=market_type,
        )

    def cancel_order(self, order_id, symbol, market_type=MarketType.PERPETUAL):
        market_symbol = self.to_market_symbol(symbol, market_type)
        return self._call("cancel_order", order_id, market_symbol, market_type=market_type)

    def get_order(self, order_id, symbol, market_type=MarketType.PERPETUAL):
        market_symbol = self.to_market_symbol(symbol, market_type)
        return self._call("fetch_order", order_id, market_symbol, market_type=market_type)

    # ---------------- 健康檢查 ----------------

    def ping(self):
        try:
            ticker = self.get_ticker("BTC/USDT", MarketType.PERPETUAL)
            return {"success": True, "last": ticker["price"]}
        except Exception as exc:
            return {"success": False, "error": str(exc)}


def _as_int(value):
    """ccxt 的 precision 可能是小數位數(int)也可能是最小單位(float)。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number.is_integer() and number >= 1:
        return int(number)
    if 0 < number < 1:
        # 0.001 -> 3 位小數
        return max(0, round(-1 * _log10(number)))
    return int(number)


def _log10(value):
    import math
    return math.log10(value)


_adapter = None


def build_bingx_adapter():
    """全域單例。測試請直接 `BingXAdapter(exchange_factory=...)`。"""
    global _adapter
    if _adapter is None:
        _adapter = BingXAdapter()
    return _adapter
