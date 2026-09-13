"""
市場資料服務。

職責:符號轉換、快取、以及**資料品質把關**。
底層取得資料的邏輯在 ExchangeAdapter,這一層不碰 ccxt。

對外契約(刻意與 Phase 1 之前一致,避免破壞生產):
    get_price(symbol)            -> float | None
    get_ohlcv(symbol, tf, limit) -> pandas.DataFrame | None

technical_service.py 與 strategy.py 直接對結果做 df["close"],
不能換成 list of dict —— 當初的重構就是在這裡 break 掉才被回退的。

Phase 2 新增:
    get_ohlcv_checked()  同時回傳 DataFrame 與 QualityReport
    get_order_book()     深度、價差、買賣失衡
"""
import logging
from typing import Dict, List, Optional, Tuple

import pandas as pd

from agmcis.config import settings
from agmcis.core.enums import MarketType
from agmcis.core.errors import ExchangeUnavailableError
from agmcis.data import quality
from cache_service import cache

logger = logging.getLogger("agmcis.market_data")

OHLCV_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

_adapter = None


def get_adapter():
    """延後建立 —— import 這個模組不該就去連交易所。"""
    global _adapter
    if _adapter is None:
        from agmcis.exchange.bingx import build_bingx_adapter
        _adapter = build_bingx_adapter()
    return _adapter


def set_adapter(adapter):
    """測試或切換到 paper / testnet 時注入。"""
    global _adapter
    _adapter = adapter


def _cache_key(kind, symbol, market_type, *parts):
    suffix = ":".join(str(p) for p in parts)
    key = f"{kind}:{market_type.value}:{symbol}"
    return f"{key}:{suffix}" if suffix else key


# ---------------- Ticker ----------------

def get_ticker(symbol, market_type=MarketType.PERPETUAL) -> Optional[Dict]:
    market_type = MarketType.parse(market_type, MarketType.PERPETUAL)
    try:
        return cache.get_or_fetch(
            _cache_key("ticker", symbol, market_type),
            settings.CACHE_TTL["ticker"],
            lambda: get_adapter().get_ticker(symbol, market_type),
        )
    except ExchangeUnavailableError as exc:
        logger.error("get_ticker failed for %s: %s", symbol, exc)
        return None


def get_price(symbol, market_type=MarketType.PERPETUAL) -> Optional[float]:
    """
    最新價。**WebSocket 優先,REST fallback**(第四十九節)。

    WebSocket 只在報價夠新的時候才算數 —— 過期就當作沒有,
    退回 REST。斷線時繼續用最後一次收到的價格,是這條路徑最危險的
    失敗模式:那個價格看起來完全正常,而系統會用它算停損與強平。
    """
    streamed = _streamed_price(symbol, market_type)
    if streamed is not None:
        return streamed

    ticker = get_ticker(symbol, market_type)
    if not ticker:
        return None
    price = ticker.get("price")
    return float(price) if price is not None else None


def _streamed_price(symbol, market_type):
    """
    WebSocket 的即時價。沒啟動、不新鮮、或出錯一律回 None。

    只用於永續:Standard Futures 沒有走 WebSocket。
    """
    if market_type is not MarketType.PERPETUAL:
        return None

    try:
        from agmcis.exchange.bingx.stream import get_stream

        stream = get_stream()
        if stream is None:
            return None
        return stream.get_price(symbol)
    except Exception as exc:
        # 串流那一層出問題不該讓取價失敗 —— REST 還在。
        logger.warning("WebSocket 取價失敗,退回 REST | %s | %s", symbol, exc)
        return None


def get_price_checked(symbol, market_type=MarketType.PERPETUAL):
    """
    回傳 (price, report)。report.ok 為 False 時 price 為 None ——
    價格是停損判斷與下單的依據,寧可沒有也不要用壞值。
    """
    ticker = get_ticker(symbol, market_type)
    report = quality.check_ticker(ticker, symbol)

    if not report.ok:
        logger.warning("Ticker 品質不合格 | %s | %s", symbol, report.summary)
        return None, report

    return float(ticker["price"]), report


# ---------------- K 線 ----------------

def _fetch_rows(symbol, timeframe, limit, market_type):
    return cache.get_or_fetch(
        _cache_key("ohlcv", symbol, market_type, timeframe, limit),
        settings.CACHE_TTL["ohlcv"],
        lambda: get_adapter().get_ohlcv(symbol, timeframe, limit, market_type),
    )


def _rows_to_frame(rows):
    df = pd.DataFrame(rows, columns=OHLCV_COLUMNS)
    df["exchange"] = "bingx"
    return df


def get_ohlcv(symbol="BTC/USDT", timeframe="1h", limit=150,
              market_type=MarketType.PERPETUAL):
    """
    回傳 pandas DataFrame,失敗回 None。

    ⚠️ 這個函式**不做**品質檢查,只保證格式。
    需要品質把關請用 get_ohlcv_checked()。
    """
    market_type = MarketType.parse(market_type, MarketType.PERPETUAL)

    try:
        rows = _fetch_rows(symbol, timeframe, limit, market_type)
    except ExchangeUnavailableError as exc:
        logger.error("get_ohlcv failed for %s %s: %s", symbol, timeframe, exc)
        return None

    if not rows:
        logger.warning("get_ohlcv returned empty for %s %s", symbol, timeframe)
        return None

    try:
        return _rows_to_frame(rows)
    except Exception as exc:
        logger.exception("get_ohlcv dataframe build failed for %s: %s", symbol, exc)
        return None


def get_ohlcv_checked(symbol, timeframe="1h", limit=150, min_candles=60,
                      market_type=MarketType.PERPETUAL) -> Tuple[Optional[pd.DataFrame], quality.QualityReport]:
    """
    回傳 (DataFrame | None, QualityReport)。

    **report.ok 為 False 時 DataFrame 是 None。** 呼叫端據此回 NO TRADE,
    不要退而求其次用有問題的資料算個大概 —— 那正是 Phase 0 稽核抓到的問題:
    系統分不出「市場中性」與「資料壞掉」。
    """
    market_type = MarketType.parse(market_type, MarketType.PERPETUAL)

    try:
        rows = _fetch_rows(symbol, timeframe, limit, market_type)
    except ExchangeUnavailableError as exc:
        report = quality.QualityReport(symbol=symbol, timeframe=timeframe)
        report.add("FETCH_FAILED", quality.SEVERITY_ERROR, str(exc))
        logger.error("get_ohlcv_checked fetch failed | %s %s | %s", symbol, timeframe, exc)
        return None, report

    report = quality.check_ohlcv(rows, symbol, timeframe, min_candles=min_candles)

    if not report.ok:
        logger.warning(
            "OHLCV 品質不合格,拒絕使用 | %s %s | %s", symbol, timeframe, report.summary,
        )
        return None, report

    if report.warnings:
        logger.info("OHLCV 品質警告 | %s %s | %s", symbol, timeframe, report.summary)

    try:
        return _rows_to_frame(rows), report
    except Exception as exc:
        report.add("FRAME_BUILD_FAILED", quality.SEVERITY_ERROR, str(exc))
        logger.exception("get_ohlcv_checked frame build failed | %s | %s", symbol, exc)
        return None, report


def get_ohlcv_dicts(symbol, timeframe="15m", limit=120,
                    market_type=MarketType.PERPETUAL) -> List[Dict]:
    df = get_ohlcv(symbol, timeframe, limit, market_type)
    if df is None:
        return []
    return df[OHLCV_COLUMNS].to_dict("records")


# ---------------- 衍生品資料 ----------------

def get_funding_rate(symbol, market_type=MarketType.PERPETUAL) -> Optional[Dict]:
    market_type = MarketType.parse(market_type, MarketType.PERPETUAL)
    return cache.get_or_fetch(
        _cache_key("funding", symbol, market_type),
        settings.CACHE_TTL["funding_rate"],
        lambda: get_adapter().get_funding_rate(symbol, market_type),
    )


def get_trade_flow(symbol, limit=500,
                   market_type=MarketType.PERPETUAL) -> Optional[Dict]:
    """
    逐筆成交的 volume delta(第三十八節)。取不到回 None。

    快取時間刻意很短:訂單流的訊息在幾十秒內就過期了,
    一份三分鐘前的 delta 描述的是另一個市場。
    """
    return _cached(
        "trade_flow", symbol, market_type,
        settings.CACHE_TTL.get("trade_flow", 15),
        lambda: get_adapter().get_trade_flow(symbol, limit, market_type),
    )


def get_open_interest(symbol, market_type=MarketType.PERPETUAL) -> Optional[Dict]:
    market_type = MarketType.parse(market_type, MarketType.PERPETUAL)
    return cache.get_or_fetch(
        _cache_key("oi", symbol, market_type),
        settings.CACHE_TTL["open_interest"],
        lambda: get_adapter().get_open_interest(symbol, market_type),
    )


def get_order_book(symbol, limit=20, market_type=MarketType.PERPETUAL) -> Optional[Dict]:
    market_type = MarketType.parse(market_type, MarketType.PERPETUAL)
    return cache.get_or_fetch(
        _cache_key("orderbook", symbol, market_type, limit),
        settings.CACHE_TTL["ticker"],
        lambda: get_adapter().get_order_book(symbol, limit, market_type),
    )


# ---------------- 綜合快照 ----------------

def _safe(label, symbol, fetch):
    """
    輔助資料的取得包一層。

    失敗回 None 但**一定會記錄** —— 「不讓 Dashboard 掛掉」不等於
    「假裝什麼都沒發生」。adapter 已經把預期中的失敗轉成 None,
    這裡攔的是預期外的例外(例如交易所回傳非預期結構)。
    """
    try:
        return fetch()
    except Exception as exc:
        logger.warning("市場快照的 %s 取得失敗 | %s | %s", label, symbol, exc)
        return None


def get_market_snapshot(symbol, market_type=MarketType.PERPETUAL) -> Dict:
    """
    給訊號層 / 儀表板用的綜合快照。
    輔助資料任一項失敗都不讓整體掛掉,缺的欄位回 None,由前端顯示「--」。
    """
    market_type = MarketType.parse(market_type, MarketType.PERPETUAL)

    ticker = _safe("ticker", symbol, lambda: get_ticker(symbol, market_type)) or {}
    funding = _safe("funding_rate", symbol, lambda: get_funding_rate(symbol, market_type))
    oi = _safe("open_interest", symbol, lambda: get_open_interest(symbol, market_type))
    book = _safe("order_book", symbol, lambda: get_order_book(symbol, market_type=market_type))

    return {
        "symbol": symbol,
        "market_type": market_type.value,
        "price": ticker.get("price"),
        "change_pct_24h": ticker.get("change_pct_24h"),
        "high_24h": ticker.get("high_24h"),
        "low_24h": ticker.get("low_24h"),
        "volume_24h": ticker.get("volume_24h"),
        "funding_rate": funding.get("funding_rate") if funding else None,
        "next_funding_time": funding.get("next_funding_time") if funding else None,
        "open_interest": oi.get("open_interest") if oi else None,
        "spread_pct": book.get("spread_pct") if book else None,
        "order_book_imbalance": book.get("imbalance") if book else None,
    }


def to_market_symbol(symbol, market_type=MarketType.PERPETUAL):
    return get_adapter().to_market_symbol(symbol, market_type)
