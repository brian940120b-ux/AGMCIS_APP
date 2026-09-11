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

    funding = get_funding_rate(symbol)
    funding_rate = funding.get("rate") if isinstance(funding, dict) else None

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
    )


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
