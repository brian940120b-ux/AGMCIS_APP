"""
ExchangeAdapter 抽象層 · 2026-09-10(PHASE 2)

═══ Master Prompt 第 7 條 ═══
「Strategy 絕對不能直接呼叫 BingX API。」

    Signal → Risk → Portfolio → Execution → **ExchangeAdapter** → BingX

這一層的意義不是「將來要換交易所」(那是次要的),而是:
**讓「策略邏輯」與「交易所細節」在結構上不可能混在一起。**

混在一起的後果本專案已經付過代價:訂單層原本直接送
`notional / price` 的全精度浮點數,因為它不知道交易所有數量精度這回事
—— 七個持倉的數量全部不合規,而紙上交易完全看不出來。

═══ 這一層**不做**的事 ═══
· 不決定要不要交易(那是 Strategy)
· 不決定能不能交易(那是 Risk Engine)
· 不決定買多少(那是 Portfolio Manager)
它只負責:**把一個已經被批准的訂單,用這個交易所聽得懂的方式送出去,
並如實回報發生了什麼。**
"""
from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from exchange.types import (Contract, MarketType, NotSupported, OrderRequest,
                            OrderResult)


class ExchangeAdapter(ABC):
    """所有交易所介面的共同契約。"""

    name: str = "abstract"
    market_type: MarketType = MarketType.PERPETUAL
    #: 這個介面會不會送出真實訂單。紙上實作必須是 False。
    is_live: bool = False

    # ── 市場資料(公開,不需金鑰)────────────────────────
    @abstractmethod
    def contracts(self) -> dict[str, Contract]:
        """全部可交易標的的規格。"""

    @abstractmethod
    def mark_prices(self, symbols: list[str] | None = None
                    ) -> dict[str, float]:
        """標記價 —— 交易所用它判定強平與未實現盈虧,不是最新成交價。"""

    @abstractmethod
    def last_prices(self, symbols: list[str] | None = None
                    ) -> dict[str, float]:
        """最新成交價。"""

    def funding_rates(self, symbol: str, since_ms: int,
                      until_ms: int) -> list[dict]:
        """區間內**實際結算**的資金費。非永續市場應拋 NotSupported。"""
        raise NotSupported(f"{self.name} 不提供資金費")

    # ── 交易(私有,需金鑰)──────────────────────────────
    @abstractmethod
    def submit(self, req: OrderRequest) -> OrderResult:
        """送出一張訂單。

        **必須回報交易所實際回傳的成交量與成交均價**,
        不得用下單量/下單價回填。
        """

    def positions(self) -> list[dict]:
        """交易所端的實際持倉 —— 對帳用。"""
        raise NotSupported(f"{self.name} 未實作持倉查詢")

    def balance(self) -> dict:
        """交易所端的帳戶餘額 —— 對帳用。"""
        raise NotSupported(f"{self.name} 未實作餘額查詢")

    def cancel_all(self, symbol: str | None = None) -> int:
        """撤銷未成交訂單。Kill Switch 會用到。"""
        raise NotSupported(f"{self.name} 未實作撤單")

    # ── 便利方法(共用實作,子類不必重寫)──────────────────
    def spec(self, symbol: str) -> Contract:
        c = self.contracts().get(symbol)
        if c is None:
            raise NotSupported(
                f"{self.name} 沒有 {symbol} 的合約規格 —— 這個標的不能下單。"
                "(不給預設值:猜一個精度只會產生一張會被拒的單)")
        return c

    def round_qty(self, symbol: str, qty: float) -> float:
        """數量調整到交易所精度。**一律無條件捨去。**

        進位會讓實際部位大於預期、佔用比預算多的保證金;捨去最多只是
        少買一點。在「可能超出保證金」與「少買一點」之間永遠選後者。
        """
        p = self.spec(symbol).quantity_precision
        f = 10 ** p
        return int(abs(qty) * f + 1e-9) / f * (1 if qty >= 0 else -1)

    def round_price(self, symbol: str, price: float) -> float:
        return round(price, self.spec(symbol).price_precision)

    def __repr__(self) -> str:
        return (f"<{type(self).__name__} {self.name} "
                f"{self.market_type.value} live={self.is_live}>")
