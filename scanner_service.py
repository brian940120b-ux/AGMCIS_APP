"""
向下相容 shim。

Phase 6 把兩條互相矛盾的訊號管線合併成一條
(`agmcis/signal/pipeline.py`)。這裡把統一管線的 `Signal` 物件
轉回舊的 dict 格式,讓既有呼叫端(api/market_scan.py、v2、v3、
auto_trader)完全不用改。

⚠️ 重點:**Dashboard 現在看到的訊號,就是實際下單依據的訊號。**
在 Phase 6 之前這兩者是不同的東西。

新程式碼請直接用:
    from agmcis.signal.pipeline import scan, top_opportunities
"""
from agmcis.config import settings
from agmcis.core.enums import Direction
from agmcis.signal.pipeline import scan as _scan

# 舊介面用 emoji 字串當訊號值。保留是為了不破壞前端,
# 但內部一律用 Direction enum。
SIGNAL_STRONG_BUY = "🟢 Strong Buy"
SIGNAL_BUY = "🟢 Buy"
SIGNAL_HOLD = "🟡 Hold"
SIGNAL_SELL = "🔴 Sell"
SIGNAL_STRONG_SELL = "🔴 Strong Sell"
SIGNAL_NO_DATA = "⚪ No Data"

STRONG_SCORE = 80


def _legacy_signal(signal):
    """把 Signal 轉成舊的 emoji 字串。"""
    if not signal.data_ok:
        return SIGNAL_NO_DATA
    if signal.direction is Direction.LONG:
        return SIGNAL_STRONG_BUY if (signal.score or 0) >= STRONG_SCORE else SIGNAL_BUY
    if signal.direction is Direction.SHORT:
        return SIGNAL_STRONG_SELL if (signal.score or 0) >= STRONG_SCORE else SIGNAL_SELL
    return SIGNAL_HOLD


def _to_legacy_dict(signal):
    indicators = getattr(signal, "indicators", {}) or {}
    consensus = getattr(signal, "consensus", {}) or {}

    return {
        "symbol": signal.symbol,
        "action": signal.direction.name if signal.direction.is_directional else "WAIT",
        "trade_signal": _legacy_signal(signal),
        "confidence": signal.score,
        "score": signal.score,
        "strategy_confidence": signal.confidence,
        "entry_price": signal.entry,
        "stoploss": signal.stop_loss,
        "takeprofit": signal.take_profit,
        "risk_reward": signal.risk_reward,
        "market_regime": signal.market_regime,
        "data_ok": signal.data_ok,
        "blocked_reason": (
            signal.reasons[0] if signal.reasons and not signal.is_tradable else None
        ),
        "reasons": list(signal.reasons),
        "agreeing_strategies": consensus.get("agreeing", []),
        "score_breakdown": getattr(signal, "score_breakdown", None),
        "regime_detail": getattr(signal, "regime_detail", None),
        "indicators": indicators,
        # 舊欄位:前端還在用。MTF 已由市況與策略集成取代。
        "mtf_score": len(consensus.get("agreeing", [])),
        "mtf_status": signal.market_regime or "UNKNOWN",
        "timeframes": {},
    }


def scan_market(symbols=None, timeframe="1h"):
    """統一管線的結果,轉成舊格式。"""
    symbols = symbols if symbols is not None else settings.WATCHLIST_SYMBOLS
    return [_to_legacy_dict(s) for s in _scan(symbols, timeframe=timeframe)]


def calculate_scanner_confidence(indicators):
    """
    向下相容。

    ⚠️ 這個函式已被 agmcis/signal/scorer.py 取代。
    它原本與 strategy.analyze_symbol() 是兩套互相矛盾的評分公式。
    保留只是為了不讓舊測試與舊呼叫端壞掉。
    """
    if isinstance(indicators, dict) and indicators.get("data_ok") is False:
        return None
    return None
