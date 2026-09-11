"""
向下相容 shim。

實作已移到 agmcis/data/market_data.py(Phase 2)。
這裡保留原本的函式名稱與回傳格式,讓既有呼叫端完全不用改。

新程式碼請直接用:
    from agmcis.data import market_data
特別是 get_ohlcv_checked() —— 它會做資料品質把關,壞資料回傳 None 而不是硬算。
"""
from agmcis.data.market_data import (  # noqa: F401
    OHLCV_COLUMNS,
    get_funding_rate,
    get_market_snapshot,
    get_ohlcv,
    get_ohlcv_checked,
    get_ohlcv_dicts,
    get_open_interest,
    get_order_book,
    get_price,
    get_price_checked,
    get_ticker,
    set_adapter,
    to_market_symbol,
)


def get_exchange_for_symbol(symbol):
    """向下相容:舊呼叫端期待 (name, exchange) 的 tuple。"""
    from agmcis.data.market_data import get_adapter
    return ("bingx", get_adapter())
