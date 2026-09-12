"""
BingX ExchangeAdapter 實作。

設計:
  - ccxt 實體透過 exchange_factory 注入,不在 import 時建立 —— 這是能寫單元測試的前提。
  - 重試策略由 error_policy 決定,不同錯誤有不同退避(限流退更久、時鐘偏移先對時)。
  - 核心行情失敗拋例外,輔助資料失敗回 None(見 base.py 的約定)。

⚠️ 為什麼用 ccxt 而不自己實作 HTTP 與簽章:
  Master Prompt 要求「不要靠模型記憶猜 API」。BingX 的端點路徑與參數會變,
  自己手寫一份簽章與端點對應表,等於把「猜」寫進程式碼。
  ccxt 是持續對著活的 API 維護的,把它當成 HTTP + 簽章層,
  我們專注在它不提供的東西:限流策略、錯誤分類、對時、合約規則快取、狀態正規化。

⚠️ Standard Futures 目前無法支援(Phase 3 實測結論,見 docs/PHASE_3_REPORT.md):
  ccxt 的 bingx 只有 spot 與 swap 兩種統一市場型態(has['future'] 是 False)。
  BingX Standard Contract 是另一套 API,在 ccxt 只有三個 private 端點
  (balance / allPosition / allOrders)—— 沒有行情、沒有合約清單、沒有下單。
  所以這裡對 Standard 一律明確拒絕,不做「看起來能跑但其實錯」的事。
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
from agmcis.exchange import error_policy
from agmcis.exchange.base import ExchangeAdapter
from agmcis.exchange.rate_limiter import RateLimiter

logger = logging.getLogger("agmcis.exchange.bingx")


def _as_float(value):
    """轉不動就回 None。回 0 會被當成「標記價是 0」,那比沒有更糟。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

# ccxt 的 bingx 只有這一種合約市場型態。
# has['future'] 是 False,傳 defaultType='futures' 建構時不會報錯,
# 但實際呼叫時才會失敗 —— 這種「延後爆炸」正是要避免的。
_CCXT_MARKET_TYPE = {
    MarketType.PERPETUAL: "swap",
}

STANDARD_UNSUPPORTED_REASON = (
    "BingX Standard Futures 目前無法透過 ccxt 支援:"
    "ccxt 的 bingx 統一 API 只涵蓋 spot 與 swap(has['future'] = False),"
    "Standard Contract 只有 balance / allPosition / allOrders 三個 private 端點,"
    "沒有行情、沒有合約清單、也沒有下單。詳見 docs/PHASE_3_REPORT.md。"
)

CAPABILITIES = frozenset({
    "ticker", "ohlcv", "tickers", "order_book", "funding_rate", "open_interest",
    "trading_rules", "perpetual_futures", "server_time", "sandbox",
})


def _build_ccxt_exchange(market_type=MarketType.PERPETUAL):
    credentials = {k: v for k, v in settings.EXCHANGE_CREDENTIALS.items() if v}
    exchange = ccxt.bingx({
        **credentials,
        "enableRateLimit": True,
        "timeout": settings.EXCHANGE_TIMEOUT_MS,
        "options": {"defaultType": _ccxt_type(market_type)},
    })
    if settings.EXCHANGE_USE_TESTNET:
        # BingX 的測試環境(VST)。Phase 11 會用到。
        exchange.set_sandbox_mode(True)
        logger.warning("[bingx] 使用測試環境 (VST) —— 這不是真實市場")
    return exchange


def _ccxt_type(market_type):
    market_type = MarketType.parse(market_type, MarketType.PERPETUAL)
    if market_type not in _CCXT_MARKET_TYPE:
        raise ExchangeUnavailableError(STANDARD_UNSUPPORTED_REASON)
    return _CCXT_MARKET_TYPE[market_type]


def _build_rate_limiter():
    """
    行程內限流,必要時再包一層跨行程限流。

    跨行程那層預設關閉 —— 它需要 migration 006 的資料表。
    表不存在時每次呼叫都會失敗一次再降級,那比不開更慢。
    """
    local = RateLimiter(
        max_calls=settings.EXCHANGE_RATE_LIMIT_CALLS,
        period_seconds=settings.EXCHANGE_RATE_LIMIT_PERIOD,
    )

    if not settings.EXCHANGE_SHARED_RATE_LIMIT:
        return local

    from agmcis.exchange.shared_rate_limit import SharedRateLimiter

    return SharedRateLimiter(local)


class BingXAdapter(ExchangeAdapter):
    name = "bingx"

    def __init__(self, exchange_factory=_build_ccxt_exchange,
                 max_retries=None, backoff_seconds=None,
                 rate_limiter=None, sleep=time.sleep):
        self._factory = exchange_factory
        self._instances = {}
        self._markets_cache = {}
        self._sleep = sleep
        self.max_retries = max_retries or settings.EXCHANGE_MAX_RETRIES
        self.backoff_seconds = (
            backoff_seconds if backoff_seconds is not None
            else settings.EXCHANGE_RETRY_BACKOFF_SECONDS
        )
        self.rate_limiter = (
            rate_limiter if rate_limiter is not None
            else _build_rate_limiter()
        )
        # 伺服器時間與本機時間的差(毫秒)。None 代表還沒對過時。
        self._time_offset_ms = None
        self._time_synced_at = None

    def capabilities(self):
        # standard_futures 不在清單裡,而且是**確定不支援**,不是尚未驗證。
        # 原因見模組頂端的 STANDARD_UNSUPPORTED_REASON。
        return CAPABILITIES

    # ---------------- 底層呼叫 ----------------

    def _instance(self, market_type):
        market_type = MarketType.parse(market_type, MarketType.PERPETUAL)
        self._require_supported(market_type)
        if market_type not in self._instances:
            self._instances[market_type] = self._factory(market_type)
        return self._instances[market_type]

    def _call(self, fn_name, *args, market_type=MarketType.PERPETUAL,
              is_write=False, **kwargs):
        """
        帶重試的交易所呼叫。

        重試策略由 error_policy 決定 —— 不是所有「網路錯誤」都該用同樣的退避:
          被限流要退更久,時鐘偏移要先對時,而寫入操作的逾時根本不該重試。
        """
        exchange = self._instance(market_type)
        fn = getattr(exchange, fn_name)
        last_error = None
        resynced = False

        for attempt in range(1, self.max_retries + 1):
            self.rate_limiter.acquire(sleep=self._sleep)

            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                last_error = exc
                policy = (
                    error_policy.classify_write_failure(exc) if is_write
                    else error_policy.classify(exc)
                )

                if policy.action == error_policy.ACTION_RECONCILE:
                    # 送出訂單後失敗:交易所可能已經收到。重送是重複開倉最常見的來源。
                    logger.error(
                        "[bingx] %s 寫入失敗且狀態不明,必須對帳不可重送: %s",
                        fn_name, error_policy.describe(exc),
                    )
                    raise policy.exception_class(
                        f"{fn_name} 狀態不明: {exc!r}。{policy.description}"
                    ) from exc

                if not policy.is_retryable or attempt >= self.max_retries:
                    logger.error(
                        "[bingx] %s 放棄 (attempt %d/%d): %s",
                        fn_name, attempt, self.max_retries, error_policy.describe(exc),
                    )
                    break

                if policy.action == error_policy.ACTION_BACKOFF_LONG:
                    cooldown = self.backoff_seconds * policy.backoff_multiplier * attempt
                    actual = self.rate_limiter.penalise(cooldown)
                    logger.warning(
                        "[bingx] %s 被限流,冷卻 %.1fs: %s",
                        fn_name, actual, error_policy.describe(exc),
                    )
                    continue

                if policy.action == error_policy.ACTION_RESYNC_TIME:
                    if resynced:
                        logger.error("[bingx] %s 對時後仍然失敗,放棄", fn_name)
                        break
                    resynced = True
                    logger.warning(
                        "[bingx] %s 時間戳被拒,重新同步伺服器時間: %s",
                        fn_name, error_policy.describe(exc),
                    )
                    self.sync_server_time(market_type=market_type)
                    continue

                delay = self.backoff_seconds * policy.backoff_multiplier * attempt
                logger.warning(
                    "[bingx] %s attempt %d/%d 失敗,%.1fs 後重試: %s",
                    fn_name, attempt, self.max_retries, delay,
                    error_policy.describe(exc),
                )
                self._sleep(delay)

        policy = error_policy.classify(last_error)
        raise policy.exception_class(
            f"{fn_name} failed on bingx ({policy.category}): {last_error!r}"
        ) from last_error

    # ---------------- 伺服器時間同步 ----------------

    def sync_server_time(self, market_type=MarketType.PERPETUAL):
        """
        取得交易所時間並記錄與本機的差。

        時鐘偏移是簽章失敗的經典原因:BingX 會拒絕時間戳偏差過大的請求,
        而錯誤訊息看起來像「簽章錯誤」,很容易被誤判成 API Key 有問題。
        回傳偏移毫秒數;失敗回 None 而不是讓呼叫端掛掉。
        """
        try:
            exchange = self._instance(market_type)
            self.rate_limiter.acquire(sleep=self._sleep)
            server_ms = exchange.fetch_time()
        except Exception as exc:
            logger.warning("[bingx] 無法取得伺服器時間: %s", exc)
            return None

        local_ms = time.time() * 1000
        self._time_offset_ms = server_ms - local_ms
        self._time_synced_at = local_ms

        if abs(self._time_offset_ms) > settings.EXCHANGE_MAX_CLOCK_SKEW_MS:
            logger.error(
                "[bingx] ⚠️ 本機時鐘與交易所相差 %.0f ms,已超過 %d ms。"
                "簽章可能被拒。請在伺服器上檢查 NTP 對時。",
                self._time_offset_ms, settings.EXCHANGE_MAX_CLOCK_SKEW_MS,
            )
        else:
            logger.info("[bingx] 時鐘偏移 %.0f ms", self._time_offset_ms)

        return self._time_offset_ms

    def clock_status(self):
        """給健康檢查用。"""
        skew = self._time_offset_ms
        return {
            "offset_ms": skew,
            "synced": skew is not None,
            "within_tolerance": (
                None if skew is None
                else abs(skew) <= settings.EXCHANGE_MAX_CLOCK_SKEW_MS
            ),
            "tolerance_ms": settings.EXCHANGE_MAX_CLOCK_SKEW_MS,
        }

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
        self._require_supported(market_type)

        symbol = str(symbol).upper().strip()
        if ":" in symbol:
            return symbol

        # ccxt 統一格式下的 USDT-M 永續:BTC/USDT:USDT
        quote = symbol.split("/")[-1]
        return f"{symbol}:{quote}"

    @staticmethod
    def _require_supported(market_type):
        """
        Standard Futures 一律明確拒絕。

        原本這裡會回傳一個「看起來合理」的符號,然後在更深的地方失敗,
        錯誤訊息完全看不出根因。早點失敗、訊息說清楚,比較好除錯。
        """
        if MarketType.parse(market_type, MarketType.PERPETUAL) not in _CCXT_MARKET_TYPE:
            raise ExchangeUnavailableError(STANDARD_UNSUPPORTED_REASON)

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

        wanted = _ccxt_type(market_type)
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
            # 標記價與指數價(第十一節)。
            #
            # 這兩個與 last 是不同的東西,而且差別會決定實際結果:
            #   * **強平用的是標記價,不是最新成交價。** 用 last 算強平距離
            #     會在插針行情裡算錯 —— 那正是最需要算對的時候。
            #   * 未實現損益也是用標記價計算的。
            #   * 指數價是現貨參考,標記價偏離指數價很多代表合約在溢價。
            #
            # ccxt 對 BingX 不保證帶回這兩個欄位,所以取不到就是 None ——
            # **不退回 last**。用 last 冒充標記價會讓呼叫端以為它拿到了
            # 標記價,而那個誤會只有在插針的時候才會被發現。
            "mark_price": self._mark_price(raw),
            "index_price": self._index_price(raw),
            "timestamp": raw.get("timestamp"),
        }

    @staticmethod
    def _mark_price(raw):
        info = raw.get("info") or {}
        for key in ("markPrice", "mark_price"):
            if raw.get(key) is not None:
                return _as_float(raw.get(key))
            if info.get(key) is not None:
                return _as_float(info.get(key))
        return None

    @staticmethod
    def _index_price(raw):
        info = raw.get("info") or {}
        for key in ("indexPrice", "index_price"):
            if raw.get(key) is not None:
                return _as_float(raw.get(key))
            if info.get(key) is not None:
                return _as_float(info.get(key))
        return None

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

        # 只有永續有資金費率。Standard 本來就不支援(見模組頂端),
        # 但即使未來支援了,它也沒有資金費率 —— 不該白打一次 API。
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
            quantity, price, request, market_type=market_type, is_write=True,
        )

    def cancel_order(self, order_id, symbol, market_type=MarketType.PERPETUAL):
        market_symbol = self.to_market_symbol(symbol, market_type)
        return self._call(
            "cancel_order", order_id, market_symbol,
            market_type=market_type, is_write=True,
        )

    def get_order(self, order_id, symbol, market_type=MarketType.PERPETUAL):
        market_symbol = self.to_market_symbol(symbol, market_type)
        return self._call("fetch_order", order_id, market_symbol, market_type=market_type)

    # ---------------- 合約設定(需要 API Key) ----------------

    def get_leverage(self, symbol, market_type=MarketType.PERPETUAL):
        market_symbol = self.to_market_symbol(symbol, market_type)
        return self._call("fetch_leverage", market_symbol, market_type=market_type)

    def get_position_mode(self, symbol=None, market_type=MarketType.PERPETUAL):
        """One-Way 還是 Hedge。下單的 positionSide 參數取決於這個。"""
        market_symbol = self.to_market_symbol(symbol, market_type) if symbol else None
        return self._call("fetch_position_mode", market_symbol, market_type=market_type)

    def set_leverage(self, leverage, symbol, market_type=MarketType.PERPETUAL):
        """⚠️ 這會改變帳戶設定。風控完成前(Phase 5)不要自動呼叫。"""
        market_symbol = self.to_market_symbol(symbol, market_type)
        return self._call(
            "set_leverage", leverage, market_symbol,
            market_type=market_type, is_write=True,
        )

    def set_margin_mode(self, margin_mode, symbol, market_type=MarketType.PERPETUAL):
        """⚠️ 這會改變帳戶設定。"""
        market_symbol = self.to_market_symbol(symbol, market_type)
        return self._call(
            "set_margin_mode", margin_mode, market_symbol,
            market_type=market_type, is_write=True,
        )

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
