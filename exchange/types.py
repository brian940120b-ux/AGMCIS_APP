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
    """市場類型 —— **這是「交易所把它放在哪個產品線」,不是合約的性質。**

    2026-09-13 訂正。原本這裡寫著:

        STANDARD = 標準:有到期日,不收資金費

    兩句都是錯的,而且錯得很有代表性 —— 它是照「標準期貨」這個
    **中文詞的字面意思**推論出來的,沒有人去問過交易所。

    BingX App 裡的「標準合約」有兩種,官方 API 文件對它們的說法是:

      · U 本位標準合約  → /openApi/contract/v1
        只有三個 GET 端點(allPosition / allOrders / balance),
        **沒有下單 API**。文件自陳 "currently in internal testing"。

      · 幣本位標準合約  → /openApi/cswap/v1
        官方文件的標題是 **"Coin-M perpetual contracts"** ——
        它是**永續**:沒有到期日,而且**有資金費**
        (/openApi/cswap/v1/market/premiumIndex 回 lastFundingRate,
        實測 2026-09-13 拿到 0.000071)。
        它跟 U 本位永續的真正差別是**以幣結算**(反向合約)。

    所以「STANDARD」在這份程式碼裡的意思,從今天起只剩一個:
    **交易所把它歸在標準合約產品線**。到期日、資金費、正向反向,
    一律看 Contract 上的欄位,**不准從這個 enum 推論**。
    """

    PERPETUAL = "PERPETUAL"     # BingX 產品線:永續合約(U 本位)
    STANDARD = "STANDARD"       # BingX 產品線:標準合約(U 本位 / 幣本位)
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
    #: 到期時間。永續(含幣本位標準合約)為 None。
    expiry: str | None = None

    #: 反向合約(幣本位):面額以 USD 計價,盈虧與保證金**以幣結算**。
    #:
    #: 這一個布林值會改變**每一條**損益與部位大小的算式:
    #:   正向(inverse=False):名目 = 數量 × 價格         盈虧以 USDT 計
    #:   反向(inverse=True) :名目 = 張數 × 面額(USD)  盈虧以幣計
    #: 反向合約的盈虧對價格是**非線性**的,做多的下檔損失有上限、
    #: 做空的上檔損失無上限 —— 跟正向合約剛好相反。
    #: account.py / paper.py 目前只寫了正向,所以 inverse=True 的標的
    #: **在帳本改寫完成前不得下單**。
    inverse: bool = False

    #: 收不收資金費。**None = 沒有人查證過,不是「不收」。**
    #:
    #: 2026-09-13 之前這裡是一個從 market_type 推導的 property,
    #: 它對「標準合約」給出的答案是錯的(見 MarketType 的說明)。
    #: 一個推導出來的錯答案,比一個承認不知道的 None 危險得多 ——
    #: 少算資金費,回測會系統性地比實際好看。
    funding: bool | None = None

    @property
    def has_funding(self) -> bool:
        """收不收資金費。**沒查證過就拋,不猜。**"""
        if self.funding is None:
            raise Unverified(
                f"{self.symbol} 收不收資金費**沒有人查證過** —— "
                "不要從市場類型猜(2026-09-13 已經證明會猜錯:"
                "BingX 幣本位「標準合約」其實是 Coin-M perpetual,有資金費)。"
                "請在建立 Contract 時明確填 funding=True/False。")
        return self.funding


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


class Unverified(ExchangeError):
    """這件事**沒有人向交易所查證過**。

    跟 NotSupported 的差別很重要:
      · NotSupported = 查證過了,交易所不提供
      · Unverified   = 還沒查,所以任何答案都是猜的

    把 Unverified 當成 False 處理,就是 2026-09-13 那個錯誤的形狀:
    「標準合約不收資金費」聽起來像一個結論,其實只是一句沒問過的話。
    """
