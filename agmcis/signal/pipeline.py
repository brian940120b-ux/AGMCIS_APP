"""
統一訊號管線 —— 系統中**唯一**產生交易訊號的地方。

Phase 0 稽核最根本的發現是系統有兩條互相矛盾的管線:

    管線 A: market_universe(12 檔硬編碼)-> technical_service
            -> emoji 字串("🟢 Strong Buy")
            用於 Dashboard

    管線 B: exchange_universe(成交量前 50)-> strategy.analyze_symbol
            -> 中文字串("做多")-> smart_ranking
            用於實際開倉

兩條的幣種池不同、評分公式不同、訊號詞彙不同。
結果是 **Dashboard 上看到的訊號,不是實際下單所依據的訊號**。

現在只有這一條。流程:

    universe -> 資料品質 gate -> 指標 -> 市況 -> 策略集成 -> 評分 -> Signal

輸出是 `Signal` 物件。Signal 可以是 WAIT —— 那是完全合法的結論。
只有 `is_tradable` 的 Signal 才能被 `TradeIntent.from_signal()` 轉成交易意圖。
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

from agmcis.analysis import indicators as indicators_module
from agmcis.analysis import regime as regime_module
from agmcis.config import settings
from agmcis.core.enums import Direction, MarketType
from agmcis.core.models import Signal
from agmcis.signal import scorer as scorer_module
from agmcis.strategy.registry import get_registry

logger = logging.getLogger("agmcis.signal_pipeline")

DEFAULT_TIMEFRAME = "1h"
DEFAULT_LIMIT = 150
MAX_WORKERS = 8


def _risk_reward(entry, stop_loss, take_profit):
    if None in (entry, stop_loss, take_profit):
        return None
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return None
    return round(abs(take_profit - entry) / risk, 2)


def analyse_symbol(symbol, timeframe=DEFAULT_TIMEFRAME, limit=DEFAULT_LIMIT,
                   btc_indicators=None, registry=None,
                   market_type=MarketType.PERPETUAL):
    """
    對單一標的跑完整分析,回傳 Signal。

    任何一個環節不合格都會回傳一個 WAIT 的 Signal 並帶上原因,
    而不是回傳 None 或拋例外 —— 呼叫端需要知道「為什麼沒有訊號」。
    """
    from agmcis.data.market_data import get_ohlcv_checked, get_price

    registry = registry or get_registry()

    def wait_signal(reason, data_ok=True, price=None):
        """
        WAIT 也是合法結論,而且很常見。

        這裡把附加欄位一併設好,讓 Signal 的形狀在所有路徑上一致 ——
        否則消費端要對每個欄位都做 getattr 防禦,遲早有人漏掉。
        """
        signal = Signal(
            symbol=symbol, market_type=market_type, direction=Direction.WAIT,
            timeframe=timeframe, strategy="pipeline",
            confidence=None, score=None, entry=price,
            data_ok=data_ok, data_error=None if data_ok else reason,
            reasons=[reason],
        )
        _attach_details(signal)
        return signal

    # ---- 1. 資料品質 gate ----
    df, report = get_ohlcv_checked(symbol, timeframe, limit=limit)

    if df is None:
        return wait_signal(f"資料品質不合格: {report.summary}", data_ok=False)

    price = get_price(symbol)

    # ---- 2. 指標 ----
    indicators = indicators_module.compute(df, symbol, timeframe, price=price)

    if not indicators.data_ok:
        return wait_signal(
            f"指標不可用: {indicators.data_error}", data_ok=False, price=price,
        )

    # ---- 3. 市況 ----
    regime = regime_module.detect(indicators, btc_indicators=btc_indicators)

    # ---- 4. 策略集成 ----
    # 把原始 K 棒一起傳進去:需要它的策略(VWAP、市場結構)不能用
    # 指標湊一個近似值 —— 那會產生一個名字對但內容不對的訊號。
    consensus = registry.consensus(
        indicators, regime, candles=_candles(df),
        order_book=_order_book(symbol),
        trade_flow=_trade_flow(symbol),
    )

    if not consensus.is_actionable:
        signal = wait_signal(consensus.blocked_reason or "沒有共識", price=price)
        signal.market_regime = regime.regime.value
        signal.reasons = list(consensus.reasons) or signal.reasons
        return signal

    entry = indicators.price
    risk_reward = _risk_reward(entry, consensus.stop_loss, consensus.take_profit)

    # ---- 5. 評分 ----
    breakdown = scorer_module.score(consensus, indicators, regime, risk_reward)

    signal = Signal(
        symbol=symbol,
        market_type=market_type,
        direction=consensus.direction,
        timeframe=timeframe,
        strategy="+".join(consensus.agreeing),
        confidence=round(consensus.confidence, 2),
        score=breakdown.total,
        entry=entry,
        stop_loss=consensus.stop_loss,
        take_profit=consensus.take_profit,
        market_regime=regime.regime.value,
        reasons=list(consensus.reasons),
        data_ok=True,
    )

    # 這些不是 Signal 的欄位,但 Dashboard 需要 —— 掛在物件上供序列化使用
    _attach_details(
        signal,
        score_breakdown=breakdown.to_dict(),
        regime_detail=regime.to_dict(),
        indicators=indicators.to_dict(),
        consensus=consensus.to_dict(),
    )
    return signal


def _attach_details(signal, score_breakdown=None, regime_detail=None,
                    indicators=None, consensus=None):
    """
    把 Dashboard 需要的細節掛到 Signal 上。

    這些不是 Signal 的核心欄位(核心欄位是交易決策需要的東西),
    但每一條路徑都會設,所以消費端不用擔心屬性不存在。
    """
    signal.score_breakdown = score_breakdown
    signal.regime_detail = regime_detail
    signal.indicators = indicators or {}
    signal.consensus = consensus or {}
    return signal


def scan(symbols=None, timeframe=DEFAULT_TIMEFRAME, limit=DEFAULT_LIMIT,
         registry=None, use_btc_reference=True, max_workers=MAX_WORKERS) -> List[Signal]:
    """
    掃描一組標的。回傳依分數排序的 Signal 清單(WAIT 的排在後面)。

    並行執行 —— 原本的實作是逐檔同步跑,50 檔要等很久。
    """
    symbols = list(symbols if symbols is not None else settings.WATCHLIST_SYMBOLS)
    registry = registry or get_registry()

    btc_indicators = None
    if use_btc_reference:
        btc_indicators = _btc_reference(timeframe, limit)

    def analyse(symbol):
        try:
            return analyse_symbol(
                symbol, timeframe, limit,
                btc_indicators=btc_indicators, registry=registry,
            )
        except Exception as exc:
            logger.exception("訊號管線失敗 | %s | %s", symbol, exc)
            return Signal(
                symbol=symbol, market_type=MarketType.PERPETUAL,
                direction=Direction.WAIT, timeframe=timeframe, strategy="pipeline",
                data_ok=False, data_error=str(exc),
                reasons=[f"管線錯誤: {exc}"],
            )

    if max_workers and len(symbols) > 1:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(symbols))) as pool:
            signals = list(pool.map(analyse, symbols))
    else:
        signals = [analyse(symbol) for symbol in symbols]

    # 可交易的排前面,再依分數排序
    signals.sort(
        key=lambda s: (s.is_tradable, s.score if s.score is not None else -1),
        reverse=True,
    )
    return signals


def _btc_reference(timeframe, limit):
    """
    取 BTC 當大盤參考。加密貨幣的個別標的大多跟著 BTC 走。
    取不到就回 None —— 那只是少一項修正,不該讓整個掃描停擺。
    """
    from agmcis.data.market_data import get_ohlcv_checked

    try:
        df, _ = get_ohlcv_checked("BTC/USDT", timeframe, limit=limit)
        if df is None:
            return None
        return indicators_module.compute(df, "BTC/USDT", timeframe)
    except Exception as exc:
        logger.warning("取不到 BTC 大盤參考: %s", exc)
        return None


def top_opportunities(signals, count=3, min_score=None):
    """
    最佳機會。

    Master Prompt 第 53 條:**沒有足夠高品質的 setup 就不要硬選 TOP 3。**
    所以這裡會過濾分數門檻,回傳的數量可能少於 count,也可能是空的。
    """
    min_score = min_score if min_score is not None else settings.MIN_SIGNAL_SCORE
    qualified = [
        s for s in signals
        if s.is_tradable and s.score is not None and s.score >= min_score
    ]
    return qualified[:count]


def _candles(frame):
    """
    DataFrame -> [{time, open, high, low, close, volume}, ...]。

    轉不出來回 None 而不是空 list:空 list 與「沒有 K 棒」在策略那邊
    是同一個結果(WAIT),但 None 說得出「我們沒給」而空 list 看起來
    像「市場上沒有 K 棒」。
    """
    if frame is None or len(frame) == 0:
        return None

    try:
        columns = {
            name: frame[name].tolist()
            for name in ("open", "high", "low", "close", "volume")
        }
        times = frame["time"].tolist() if "time" in frame else list(range(len(frame)))

        return [
            {
                "time": times[i],
                "open": float(columns["open"][i]),
                "high": float(columns["high"][i]),
                "low": float(columns["low"][i]),
                "close": float(columns["close"][i]),
                "volume": float(columns["volume"][i]),
            }
            for i in range(len(frame))
        ]
    except Exception:
        logger.warning("K 棒轉換失敗,需要 K 棒的策略這一輪會棄權")
        return None


def _trade_flow(symbol):
    """
    逐筆成交的 volume delta(第三十八節)。取不到回 None ——
    需要它的策略會 WAIT,而那是正確的。

    與 _order_book 分開是刻意的:訂單簿是掛著的單,逐筆成交是已經
    發生的事。把兩者混成一個「市場情緒」欄位,就再也分不出訊號
    是來自可以被撤掉的掛單,還是來自撤不掉的成交。
    """
    from agmcis.config import settings

    if not getattr(settings, "AGENT_ENRICHMENT_ENABLED", True):
        return None

    try:
        from agmcis.data.market_data import get_trade_flow
        return get_trade_flow(symbol)
    except Exception as exc:
        logger.warning("訊號管線 | %s 取不到逐筆成交 | %s", symbol, exc)
        return None


def _order_book(symbol):
    """
    訂單簿。取不到回 None —— 需要它的策略會 WAIT,而那是正確的:
    一個用別的東西湊出來的「訂單流」訊號不是訂單流訊號。
    """
    from agmcis.config import settings

    if not getattr(settings, "AGENT_ENRICHMENT_ENABLED", True):
        return None

    try:
        from agmcis.data.market_data import get_order_book
        return get_order_book(symbol)
    except Exception as exc:
        logger.warning("訊號管線 | %s 取不到訂單簿 | %s", symbol, exc)
        return None
