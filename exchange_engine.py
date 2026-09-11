"""
向下相容 shim。

實作已移到 agmcis/exchange/(Phase 2):
    agmcis/exchange/base.py            ExchangeAdapter 介面
    agmcis/exchange/bingx/adapter.py   BingX 實作

這裡保留原本的模組層函式與 ExchangeEngine class,讓既有呼叫端
(api/system_health.py、tests/test_exchange_engine.py)完全不用改。

新程式碼請直接用:
    from agmcis.exchange.bingx import build_bingx_adapter
"""
from agmcis.core.enums import MarketType
from agmcis.core.errors import ExchangeUnavailableError  # noqa: F401
from agmcis.exchange.bingx.adapter import (  # noqa: F401
    BingXAdapter,
    _build_ccxt_exchange,
    build_bingx_adapter,
)


def to_contract_symbol(symbol: str) -> str:
    return build_bingx_adapter().to_market_symbol(symbol, MarketType.PERPETUAL)


class ExchangeEngine(BingXAdapter):
    """
    向下相容的名稱。

    舊介面用單一位置參數的 exchange_factory(不帶 market_type),
    這裡包一層讓兩種簽名都能用。
    """

    def __init__(self, exchange_factory=None, **kwargs):
        if exchange_factory is None:
            super().__init__(**kwargs)
            return

        def factory(market_type=MarketType.PERPETUAL):
            try:
                return exchange_factory(market_type)
            except TypeError:
                return exchange_factory()

        super().__init__(exchange_factory=factory, **kwargs)

    def get_ticker(self, symbol, market_type=MarketType.PERPETUAL):
        """舊測試直接傳合約符號並期待原樣回傳。"""
        result = super().get_ticker(symbol, market_type)
        result["symbol"] = symbol
        return result

    def get_ohlcv(self, symbol, timeframe="15m", limit=120,
                  market_type=MarketType.PERPETUAL):
        """舊介面回傳 list of dict;新介面回傳 ccxt 原始列。"""
        rows = super().get_ohlcv(symbol, timeframe, limit, market_type)
        return [
            {"time": r[0], "open": r[1], "high": r[2],
             "low": r[3], "close": r[4], "volume": r[5]}
            for r in rows
        ]

    def get_ohlcv_raw(self, symbol, timeframe="15m", limit=120,
                      market_type=MarketType.PERPETUAL):
        return BingXAdapter.get_ohlcv(self, symbol, timeframe, limit, market_type)

    def create_order(self, symbol, side, amount, order_type="market",
                     price=None, params=None, **kwargs):
        """舊簽名是 (symbol, side, amount, order_type, price, params)。"""
        return super().create_order(
            symbol=symbol, side=side, quantity=amount, order_type=order_type,
            price=price, params=params, **kwargs,
        )


engine = build_bingx_adapter()
EXCHANGES = {"bingx": engine}


def get_exchange(name):
    return EXCHANGES.get(name)


def test_connection():
    return {"bingx": engine.ping()}


def fetch_ticker_safe(symbol):
    try:
        return {"exchange": "bingx", "ticker": engine.get_ticker(symbol)}
    except ExchangeUnavailableError as exc:
        import logging
        logging.getLogger("agmcis.exchange_engine").error(
            "fetch_ticker_safe failed for %s: %s", symbol, exc
        )
        return None


def get_price_safe(symbol):
    result = fetch_ticker_safe(symbol)
    if not result:
        return None
    return {
        "symbol": symbol,
        "price": result["ticker"].get("price"),
        "exchange": "bingx",
    }


def fetch_ohlcv_safe(symbol, timeframe="1h", limit=200):
    try:
        return {"exchange": "bingx", "ohlcv": engine.get_ohlcv(symbol, timeframe, limit)}
    except ExchangeUnavailableError as exc:
        import logging
        logging.getLogger("agmcis.exchange_engine").error(
            "fetch_ohlcv_safe failed for %s: %s", symbol, exc
        )
        return None
