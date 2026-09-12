"""
把資料層接到 Agent 層。

這個模組刻意**不**放在 `agmcis/agents/` 裡面。
Agent 套件不 import 任何資料或交易所模組,那條界線由 AST 測試守著;
取資料這件事就放在這裡,由外面把資料餵進去。

    market_data -> indicators -> regime -> AgentContext
        -> agents.deliberate() -> Deliberation(可能含 TradeIntent)
        -> [呼叫端] Risk Engine hard gate

**這一層不下單,也不決定部位大小。**
"""
import logging

from agmcis.agents.base import AgentContext
from agmcis.agents.registry import deliberate as _deliberate
from agmcis.analysis import indicators as indicators_module
from agmcis.analysis import regime as regime_module
from agmcis.core.enums import MarketType

logger = logging.getLogger("agmcis.signal.agent_pipeline")

DEFAULT_TIMEFRAME = "1h"
DEFAULT_LIMIT = 150


def build_context(symbol, timeframe=DEFAULT_TIMEFRAME, limit=DEFAULT_LIMIT,
                  btc_indicators=None, position=None,
                  correlated_symbols=None, news_impact=None,
                  market_type=MarketType.PERPETUAL):
    """
    組出 Agent 要看的資料。

    取不到的東西一律留 None —— Agent 會因此棄權,而棄權不是反對票。
    **絕不用 0 或預設值填補缺失資料**,那會讓「沒資料」看起來像「數值正常」。
    """
    from agmcis.data.market_data import (
        get_funding_rate, get_ohlcv_checked, get_price,
    )

    df, report = get_ohlcv_checked(symbol, timeframe, limit=limit)

    if df is None:
        logger.info("Agent 管線 | %s 資料品質不合格 | %s", symbol, report.summary)
        # data_ok=False 的 Indicators 會讓所有 Agent 棄權
        broken = indicators_module.Indicators(
            symbol=symbol, timeframe=timeframe,
            data_ok=False, data_error=report.summary,
        )
        return AgentContext(symbol=symbol, timeframe=timeframe, indicators=broken)

    price = get_price(symbol)
    indicators = indicators_module.compute(df, symbol, timeframe, price=price)
    regime = regime_module.detect(indicators, btc_indicators=btc_indicators)

    # ⚠️ 欄位名是 adapter 定的:funding_rate,不是 rate。
    # 我第一版寫成 funding.get("rate"),那會永遠拿到 None ——
    # FundingAgent 就永遠棄權,而且完全不會報錯。有測試把這個名字釘住。
    funding = get_funding_rate(symbol)
    funding_rate = funding.get("funding_rate") if isinstance(funding, dict) else None

    return AgentContext(
        symbol=symbol,
        timeframe=timeframe,
        indicators=indicators,
        regime=regime,
        price=price if price is not None else indicators.price,
        funding_rate=funding_rate,
        news_impact=news_impact,
        btc_indicators=btc_indicators,
        position=position,
        correlated_symbols=list(correlated_symbols or []),
        # 以下四項取不到一律 None。**絕不用 0 填補** ——
        # 0 在這些欄位裡的意思分別是「多空平衡」「市場中性」
        # 「沒有事件」,那是三個很有信心的判斷,而我們其實不知道。
        **enrich(symbol)
    )


def enrich(symbol):
    """
    訂單簿、多空比、情緒、事件風險。

    集中在一個函式裡有兩個理由:
      * 這是唯一會為了「加分項」而多打外部 API 的地方,
        要關掉的時候只有一個開關。
      * 測試只需要 patch 這一個函式,不用逐一 patch 四個資料源。

    `AGENT_ENRICHMENT_ENABLED=false` 會讓它全部回 None ——
    那不是「假裝資料是中性的」,對應的 Agent 會棄權。
    """
    from agmcis.config import settings

    if not getattr(settings, "AGENT_ENRICHMENT_ENABLED", True):
        return {
            "order_book": None, "long_short_ratio": None,
            "sentiment_score": None, "news_risk": None,
        }

    from agmcis.data.market_data import get_order_book

    return {
        "order_book": _optional(
            "order_book", symbol, lambda: get_order_book(symbol),
        ),
        "long_short_ratio": _long_short_ratio(symbol),
        "sentiment_score": _sentiment_score(),
        "news_risk": _news_risk(),
    }


def _optional(label, symbol, fetch):
    """
    取一項可有可無的資料。失敗回 None 並記錄。

    不記錄的話,「這個 Agent 永遠棄權」會看起來像它的設計,
    而不是像一個壞了三個月的資料來源。
    """
    try:
        return fetch()
    except Exception as exc:
        logger.warning(
            "Agent 管線 | %s 取不到 %s | %s: %s",
            symbol, label, type(exc).__name__, exc,
        )
        return None


def _long_short_ratio(symbol):
    def fetch():
        from agmcis.data.market_data import get_adapter

        payload = get_adapter().get_long_short_ratio(symbol)
        return payload.get("ratio") if isinstance(payload, dict) else None

    return _optional("long_short_ratio", symbol, fetch)


def _sentiment_score():
    """
    整體市場情緒。目前用新聞的多空計數近似 ——
    這是一個粗糙的代理指標,不是恐懼貪婪指數。

    真正的情緒指標需要資金費率、期權偏斜、社群量能等等,
    而那些目前都拿不到。用一個粗糙的代理值比不用好,
    但 SentimentAgent 的權重與門檻是照「這是粗糙代理值」設的。
    """
    def fetch():
        from news_ai_engine import analyze_news_sentiment

        items = analyze_news_sentiment() or []
        if len(items) < 5:
            # 樣本太少的「情緒」只是那幾則新聞,不是市場情緒。
            return None

        bullish = sum(1 for i in items if i.get("sentiment") == "Bullish")
        bearish = sum(1 for i in items if i.get("sentiment") == "Bearish")
        total = bullish + bearish

        if total == 0:
            return None

        return round((bullish - bearish) / total * 100, 2)

    return _optional("sentiment", "market", fetch)


def _news_risk():
    def fetch():
        from agmcis.risk import news_risk
        return news_risk.current()

    return _optional("news_risk", "market", fetch)


def analyse_symbol(symbol, timeframe=DEFAULT_TIMEFRAME, limit=DEFAULT_LIMIT,
                   registry=None, supervisor=None,
                   market_type=MarketType.PERPETUAL, **context_kwargs):
    """
    回傳 (Deliberation, SupervisorReport)。

    **Deliberation 裡的 TradeIntent 還沒有通過風控。**
    呼叫端必須把它送進 Risk Engine,那才是決定要不要開、開多大的地方。
    """
    context = build_context(
        symbol, timeframe=timeframe, limit=limit,
        market_type=market_type, **context_kwargs
    )

    return _deliberate(
        context, registry=registry, supervisor=supervisor,
        market_type=market_type,
    )


def scan(symbols, timeframe=DEFAULT_TIMEFRAME, limit=DEFAULT_LIMIT,
         registry=None, supervisor=None, positions=None, **context_kwargs):
    """
    掃一批標的。

    單一標的失敗不會中斷整批 —— 但失敗會被記錄下來,不是靜靜跳過。
    """
    positions = positions or {}
    results = []

    for symbol in symbols:
        try:
            deliberation, report = analyse_symbol(
                symbol, timeframe=timeframe, limit=limit,
                registry=registry, supervisor=supervisor,
                position=positions.get(symbol), **context_kwargs
            )
            results.append((deliberation, report))
        except Exception as exc:
            logger.exception("Agent 管線在 %s 上失敗", symbol)
            results.append((None, None))

    return results


def actionable_intents(scan_results):
    """從掃描結果中取出真的可以送進風控的 TradeIntent。"""
    return [
        deliberation.intent
        for deliberation, _ in scan_results
        if deliberation is not None and deliberation.is_actionable
    ]
