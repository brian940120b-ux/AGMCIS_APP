"""
BingX ExchangeAdapter —— **組裝點**(Master Prompt 第八節)。

第八節建議把 adapter 拆成一組模組。這個檔案原本是 711 行、把全部
責任放在一個類別裡;現在它只做一件事:**把那些模組組成一個物件**。

## 模組對照

第八節列的每一項都對應到一個地方。已經存在的**沒有重複建立**
(第八節:「如果現有專案已有類似模組:不要重複建立。應該:
REFACTOR EXISTING CODE」),那幾個檔案是指路的 re-export:

    第八節建議          這個系統
    ---------------     -------------------------------------------
    base.py             agmcis/exchange/base.py
    client.py           bingx/client.py          ← 這次拆出來
    auth.py             bingx/auth.py            ← 這次拆出來
    signer.py           bingx/signer.py          ← ccxt 負責,說明為什麼
    market.py           bingx/market.py          ← 這次拆出來
    contracts.py        bingx/contracts.py       → agmcis/exchange/specs.py
    trading_rules.py    bingx/trading_rules.py   ← 這次拆出來
    account.py          bingx/account.py         ← 這次拆出來
    orders.py           bingx/orders.py          ← 這次拆出來
    positions.py        bingx/positions.py       ← 這次拆出來
    websocket.py        bingx/websocket.py       → bingx/stream.py
    executor.py         bingx/executor.py        → agmcis/execution/engine.py
    reconciler.py       bingx/reconciler.py      → agmcis/execution/reconciliation.py
    rate_limiter.py     bingx/rate_limiter.py    → agmcis/exchange/rate_limiter.py
    errors.py           bingx/errors.py          → agmcis/exchange/error_policy.py

## 為什麼用 mixin 而不是組合

因為 `BingXAdapter` 是 `ExchangeAdapter` 的實作,而那個介面是一組
方法。改成「adapter 持有一個 market 物件」的話,每一個方法都要寫一行
轉呼叫 —— 十五行沒有內容的程式碼,而且加一個方法就要記得加兩個地方。

mixin 讓檔案分開而物件不變:**公開 API 與拆之前完全一樣**,
所有既有的呼叫端與測試都不用改。`tests/test_bingx_modules.py`
有一條測試在盯這件事。

## 兩個不變的約定

  * 核心行情失敗**拋例外**,輔助資料失敗**回 None**(見 base.py)。
  * Standard Futures 一律明確拒絕 —— 不做「看起來能跑但其實錯」的事。
    原因見 client.py 的 STANDARD_UNSUPPORTED_REASON。
"""
from agmcis.exchange.base import ExchangeAdapter
from agmcis.exchange.bingx.account import AccountMixin
from agmcis.exchange.bingx.client import (  # noqa: F401  向下相容的 re-export
    CCXT_MARKET_TYPE,
    STANDARD_UNSUPPORTED_REASON,
    BingXClient,
    build_ccxt_exchange,
    build_rate_limiter,
    ccxt_type,
)
from agmcis.exchange.bingx.market import MarketMixin, as_float
from agmcis.exchange.bingx.orders import OrdersMixin
from agmcis.exchange.bingx.positions import PositionsMixin
from agmcis.exchange.bingx.trading_rules import TradingRulesMixin

CAPABILITIES = frozenset({
    "ticker", "ohlcv", "tickers", "order_book", "funding_rate", "open_interest",
    "trading_rules", "perpetual_futures", "server_time", "sandbox",
})


class BingXAdapter(
    MarketMixin,
    TradingRulesMixin,
    AccountMixin,
    OrdersMixin,
    PositionsMixin,
    BingXClient,
    ExchangeAdapter,
):
    """
    BingX。組裝說明見模組開頭。

    MRO 的順序有意義:mixin 在 BingXClient 之前,所以它們覆寫得了
    client 的方法(目前沒有覆寫,但順序反過來會讓未來的覆寫默默失效)。
    """

    name = "bingx"

    def capabilities(self):
        # standard_futures 不在清單裡,而且是**確定不支援**,不是尚未驗證。
        # 原因見 client.py 的 STANDARD_UNSUPPORTED_REASON。
        return CAPABILITIES


# 舊名稱。拆檔之前這幾個是模組層級的私有函式,有測試直接用它們。
_build_ccxt_exchange = build_ccxt_exchange
_ccxt_type = ccxt_type
_build_rate_limiter = build_rate_limiter
_as_float = as_float
_CCXT_MARKET_TYPE = CCXT_MARKET_TYPE


_adapter = None


def build_bingx_adapter():
    """全域單例。測試請直接 `BingXAdapter(exchange_factory=...)`。"""
    global _adapter
    if _adapter is None:
        _adapter = BingXAdapter()
    return _adapter
