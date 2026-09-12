"""
BingX 合約規則(Master Prompt 第八節的 trading_rules.py、第十七節)。

## 為什麼規則一定要從交易所拿

第十七節:交易規則不可寫死。tick size、step size、最小名目、
槓桿上限每一檔都不同,而且會變。寫死一組「常見值」的後果是
下單被拒,而錯誤訊息通常只說「參數錯誤」。

規則的**註冊表與快取**在 `agmcis/exchange/trading_rules.py`(不分交易所);
這裡只負責把 ccxt 的 market dict 翻譯成 TradingRules。
"""
from datetime import datetime, timezone

from agmcis.core.enums import MarketType
from agmcis.core.errors import ExchangeUnavailableError
from agmcis.core.models import TradingRules
from agmcis.exchange.bingx.contracts import decimals


class TradingRulesMixin:
    """BingXAdapter 的一部分。需要 client 層的 load_markets / to_market_symbol。"""

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
            price_precision=decimals(precision.get("price")),
            qty_precision=decimals(precision.get("amount")),
            max_leverage=leverage_limits.get("max"),
            fetched_at=datetime.now(timezone.utc),
        )
