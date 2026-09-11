"""
向下相容 shim。

Phase 6 之前這裡是**第二條訊號管線**:
    exchange_universe(成交量前 50)-> strategy.analyze_symbol
    -> 中文訊號 -> 加上 volume_score / news_impact / optimizer_bonus

它與 scanner_service 那條用不同的幣種池、不同的評分公式、不同的訊號詞彙,
而且**實際開倉走的是這一條**,Dashboard 顯示的卻是另一條。

現在兩邊都走 agmcis/signal/pipeline.py。這個檔案只負責:
    1. 決定要掃哪些標的(成交量前 N)
    2. 把統一管線的結果轉回舊的 ranking 格式

新程式碼請直接用:
    from agmcis.signal.pipeline import scan, top_opportunities
"""
import logging

from agmcis.core.enums import Direction
from agmcis.signal.pipeline import scan
from exchange_universe import get_top_volume_symbols
from news_impact import get_news_impact
from optimizer_bonus import get_optimizer_bonus
from symbol_filter import filter_symbols

logger = logging.getLogger("agmcis.smart_ranking")

DEFAULT_UNIVERSE_SIZE = 30


def get_smart_ranking(limit=DEFAULT_UNIVERSE_SIZE, timeframe="1h"):
    """
    成交量前 N 檔的統一管線掃描結果,依分數排序。

    回傳格式與舊版相同(symbol / score / signal / price ...),
    呼叫端(opportunity_scanner、rebalance_engine)不用改。
    """
    market = filter_symbols(get_top_volume_symbols(limit=limit))

    if not market:
        logger.warning("可交易標的清單是空的,不產生任何排名")
        return []

    volume_by_symbol = {item["symbol"]: item.get("volume", 0) for item in market}
    signals = scan(list(volume_by_symbol), timeframe=timeframe)

    ranking = []

    for signal in signals:
        # news 與 optimizer 是外部加權,不影響管線本身的評分
        try:
            news_bonus = get_news_impact(signal.symbol)
        except Exception as exc:
            logger.warning("news impact 取得失敗 | %s | %s", signal.symbol, exc)
            news_bonus = 0

        try:
            optimizer_bonus = get_optimizer_bonus(signal.symbol)
        except Exception as exc:
            logger.warning("optimizer bonus 取得失敗 | %s | %s", signal.symbol, exc)
            optimizer_bonus = 0

        base_score = signal.score if signal.score is not None else 0
        final_score = max(0, min(100, base_score + news_bonus + optimizer_bonus))

        ranking.append({
            "symbol": signal.symbol,
            "score": round(final_score, 2),
            "pipeline_score": base_score,
            "news_impact": news_bonus,
            "optimizer_bonus": optimizer_bonus,
            "signal": signal.direction.value,
            "direction": signal.direction.value,
            "price": signal.entry,
            "entry": signal.entry,
            "stoploss": signal.stop_loss,
            "takeprofit": signal.take_profit,
            "risk_reward": signal.risk_reward,
            "market_regime": signal.market_regime,
            "data_ok": signal.data_ok,
            "tradable": signal.is_tradable,
            "reasons": list(signal.reasons),
            "volume": round(volume_by_symbol.get(signal.symbol, 0), 2),
            "exchanges": ["bingx"],
        })

    ranking.sort(key=lambda item: item["score"], reverse=True)
    return ranking
