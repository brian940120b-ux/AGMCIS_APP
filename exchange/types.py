"""
交易所統一型別 · 2026-09-10(PHASE 2)

═══ 為什麼 market_type 必須是型別,不是字串 ═══
Master Prompt 第 6 條:「不要把 Standard Futures 與 Perpetual Futures
混成一種市場。」

在此之前,整個系統隱含地假設「合約 = 永續」——所有端點都是
/openApi/swap/,帳本裡沒有任何欄位記錄「這是哪一種市場」。
那個假設在只有一種市場時無害,在加入第二種時會變成無聲的錯:
兩種市場的資金費、到期、槓桿上限、保證金規則全都不同,而
「靜靜用錯規則」正是本專案一路在防的那類錯。

所以先把型別立起來,即使 Standard 目前還不能實作(見 bingx/standard.py)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class MarketType(str, Enum):
    """市場類型。**任何合約規格、帳本、訂單都必須標明。**"""

    PERPETUAL = "PERPETUAL"     # 永續:無到期日,收資金費
    STANDARD = "STANDARD"       # 標準:有到期日,不收資金費
    SPOT = "SPOT"               # 現貨:無槓桿、無強平


class PositionSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    BOTH = "BOTH"               # 單向持倉模式


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    POST_ONLY = "POST_ONLY"     # 只掛單:會立即成交就取消(保證 maker 費率)
    STOP_MARKET = "STOP_MARKET"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"


class OrderStatus(str, Enum):
    """訂單狀態機(Master Prompt 第 15 條)。

    **UNKNOWN 是最重要的一個**:網路逾時、交易所回應遺失時,我們不知道
    單子是否已經送達。此時**禁止直接重下** —— 必須先查詢交易所、對帳、
    再決定。重下會造成重複部位,而那是無法用「再平一次」修好的。
    """

    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    RISK_CHECK = "RISK_CHECK"
    SUBMITTING = "SUBMITTING"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    TIMEOUT = "TIMEOUT"
    UNKNOWN = "UNKNOWN"         # ← 禁止直接重下
    FAILED = "FAILED"


@dataclass(frozen=True)
class Contract:
    """一個可交易標的的完整規格。**規格來自交易所,不是我們寫的常數。**"""

    symbol: str
    market_type: MarketType
    quantity_precision: int
    price_precision: int
    min_qty: float
    min_notional: float
    taker_fee_pct: float
    maker_fee_pct: float
    contract_size: float = 1.0
    margin_asset: str = "USDT"
    settlement_asset: str = "USDT"
    tradable: bool = True
    # 標準合約才有:到期時間(永續為 None)
    expiry: str | None = None

    @property
    def has_funding(self) -> bool:
        """只有永續收資金費。標準合約靠到期收斂,不收資金費。"""
        return self.market_type is MarketType.PERPETUAL


@dataclass
class OrderRequest:
    """送往交易所的訂單。**不含任何交易所專屬欄位** —— 那是 Adapter 的事。"""

    symbol: str
    market_type: MarketType
    side: OrderSide
    order_type: OrderType
    qty: float
    price: float | None = None
    position_side: PositionSide = PositionSide.BOTH
    reduce_only: bool = False
    client_order_id: str = ""
    stop_price: float | None = None


@dataclass
class OrderResult:
    """交易所回傳的成交結果。

    filled_qty 與 avg_price 必須是**交易所實際回報的值**,
    不得用下單量/下單價回填 —— 那會讓帳本記進沒成交的部位,
    而訂單記錄顯示成功、帳本平衡、面板正常,沒有一條檢查會發現。
    """

    status: OrderStatus
    symbol: str
    market_type: MarketType
    client_order_id: str = ""
    exchange_order_id: str = ""
    ordered_qty: float = 0.0
    filled_qty: float = 0.0
    avg_price: float = 0.0
    fee: float = 0.0
    error: str = ""
    raw: dict = field(default_factory=dict)


class ExchangeError(RuntimeError):
    """交易所錯誤。retryable 決定能不能重試 —— 不可重試的錯誤重試只會更糟。"""

    def __init__(self, message: str, code: int | None = None,
                 retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class NotSupported(ExchangeError):
    """這個交易所/市場不支援這個操作。**明確拋出,不要靜靜回 None。**"""
