"""
BingX 底層呼叫(Master Prompt 第八節的 client.py)。

這一層負責「怎麼把一個請求送出去並拿回結果」,不負責「請求的內容
是什麼意思」。上層的 market / orders / account / positions 都透過
`_call()` 與 `_optional()` 走這裡。

## 這裡的四個責任

  1. **ccxt 實體的生命週期。** 依市場型態各一個,而且是延後建立的 ——
     在 import 時建立會讓單元測試需要網路。
  2. **重試策略。** 由 error_policy 決定,不是所有「網路錯誤」都該用
     同樣的退避:被限流要退更久,時鐘偏移要先對時,而寫入操作的
     逾時**根本不該重試**。
  3. **對時。** 見 signer.py —— 這是唯一與簽章有關、而且必須由我們
     負責的事。
  4. **核心與輔助的分野。** `_call()` 失敗拋例外,`_optional()` 失敗回
     None。約定寫在 base.py。
"""
import logging
import time

import ccxt

from agmcis.config import settings
from agmcis.core.enums import MarketType
from agmcis.core.errors import ExchangeUnavailableError
from agmcis.exchange import error_policy
from agmcis.exchange.bingx import auth
from agmcis.exchange.rate_limiter import RateLimiter

logger = logging.getLogger("agmcis.exchange.bingx")

# ccxt 的 bingx 只有這一種合約市場型態。
# has['future'] 是 False,傳 defaultType='futures' 建構時不會報錯,
# 但實際呼叫時才會失敗 —— 這種「延後爆炸」正是要避免的。
CCXT_MARKET_TYPE = {
    MarketType.PERPETUAL: "swap",
}

STANDARD_UNSUPPORTED_REASON = (
    "BingX Standard Futures 目前無法透過 ccxt 支援:"
    "ccxt 的 bingx 統一 API 只涵蓋 spot 與 swap(has['future'] = False),"
    "Standard Contract 只有 balance / allPosition / allOrders 三個 private 端點,"
    "沒有行情、沒有合約清單、也沒有下單。詳見 docs/PHASE_3_REPORT.md。"
)


def ccxt_type(market_type):
    market_type = MarketType.parse(market_type, MarketType.PERPETUAL)
    if market_type not in CCXT_MARKET_TYPE:
        raise ExchangeUnavailableError(STANDARD_UNSUPPORTED_REASON)
    return CCXT_MARKET_TYPE[market_type]


def build_ccxt_exchange(market_type=MarketType.PERPETUAL):
    """
    建一個 ccxt 實體。憑證走 auth.py —— 見那個模組說明。
    """
    exchange = ccxt.bingx({
        **auth.credentials(),
        "enableRateLimit": True,
        "timeout": settings.EXCHANGE_TIMEOUT_MS,
        "options": {"defaultType": ccxt_type(market_type)},
    })

    if settings.EXCHANGE_USE_TESTNET:
        # BingX 的測試環境(VST)。Phase 11 會用到。
        exchange.set_sandbox_mode(True)
        logger.warning("[bingx] 使用測試環境 (VST) —— 這不是真實市場")

    return exchange


def build_rate_limiter():
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


class BingXClient:
    """
    底層呼叫層。BingXAdapter 繼承它 —— 見 adapter.py 的組裝說明。
    """

    name = "bingx"

    def __init__(self, exchange_factory=build_ccxt_exchange,
                 max_retries=None, backoff_seconds=None,
                 rate_limiter=None, sleep=time.sleep):
        self._factory = exchange_factory
        self._instances = {}
        self._markets_cache = {}
        # 交易所明說不支援的端點。只問一次,見 _optional()。
        self._unsupported = set()
        self._sleep = sleep
        self.max_retries = max_retries or settings.EXCHANGE_MAX_RETRIES
        self.backoff_seconds = (
            backoff_seconds if backoff_seconds is not None
            else settings.EXCHANGE_RETRY_BACKOFF_SECONDS
        )
        self.rate_limiter = (
            rate_limiter if rate_limiter is not None
            else build_rate_limiter()
        )
        # 伺服器時間與本機時間的差(毫秒)。None 代表還沒對過時。
        self._time_offset_ms = None
        self._time_synced_at = None

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

    def _optional(self, fn_name, *args, market_type=MarketType.PERPETUAL, **kwargs):
        """
        輔助資料:失敗回 None 而不是拋例外。

        **交易所明說不支援的端點只問一次。** ccxt 對某些端點會直接拋
        NotSupported,而那不會因為重試而改變 —— 每一輪掃描都重試一次
        等於每個標的浪費兩次退避,而且 log 會被同一行警告淹沒到
        真正的問題看不見。

        注意這只記住「不支援」,不記住「暫時失敗」:網路錯誤下一次
        可能就好了,把它也記起來會讓一次網路抖動變成永久失能。
        """
        if fn_name in self._unsupported:
            return None

        try:
            return self._call(fn_name, *args, market_type=market_type, **kwargs)
        except ExchangeUnavailableError as exc:
            if "not_supported" in str(exc) or "NotSupported" in str(exc):
                self._unsupported.add(fn_name)
                logger.warning(
                    "[bingx] %s 這個交易所不支援,之後不再嘗試", fn_name,
                )
            else:
                logger.warning("[bingx] %s unavailable: %s", fn_name, exc)
            return None

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

    # ---------------- 市場符號 ----------------

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
        if MarketType.parse(market_type, MarketType.PERPETUAL) not in CCXT_MARKET_TYPE:
            raise ExchangeUnavailableError(STANDARD_UNSUPPORTED_REASON)

    def load_markets(self, market_type=MarketType.PERPETUAL, reload=False):
        market_type = MarketType.parse(market_type, MarketType.PERPETUAL)
        if reload or market_type not in self._markets_cache:
            self._markets_cache[market_type] = self._call(
                "load_markets", market_type=market_type
            ) or {}
        return self._markets_cache[market_type]

    # ---------------- 健康檢查 ----------------

    def ping(self):
        try:
            ticker = self.get_ticker("BTC/USDT", MarketType.PERPETUAL)
            return {"success": True, "last": ticker["price"]}
        except Exception as exc:
            return {"success": False, "error": str(exc)}
