"""
Broker 介面:Execution Engine 與「真的送單的東西」之間的那道縫。

存在的理由有兩個:

  1. **Paper 與 Live 走同一條執行路徑。** 兩套各自實作的下單流程,
     模擬盤驗證過的東西在實盤不算數 —— Phase 6 與 Phase 9 已經收掉過
     兩次重複的評分路徑,執行層不要再長出第三次。

  2. **實單能力必須是可以整個拔掉的。** LiveBroker 在 Phase 17 的
     Safety Gate 通過之前根本不存在。系統現在沒有任何一條路徑
     能送出真實訂單,不是靠設定擋住,是靠沒有那個實作。
"""
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class FillResult:
    """送單的結果。成交價是**實際成交價**,不是下單時看到的價格。"""
    ok: bool
    filled_quantity: float = 0.0
    average_price: Optional[float] = None
    exchange_order_id: Optional[str] = None
    reason: Optional[str] = None
    raw: Dict = field(default_factory=dict)

    @property
    def is_filled(self):
        return self.ok and self.filled_quantity > 0


class Broker:
    """所有 broker 的介面。"""

    name = "base"
    is_live = False

    def submit_entry(self, order_request, intent, size_usdt, leverage) -> FillResult:
        raise NotImplementedError

    def has_protection(self, symbol) -> bool:
        """這個部位現在有沒有停損保護。"""
        raise NotImplementedError

    def close_position(self, symbol, price=None, reason="") -> FillResult:
        raise NotImplementedError

    def get_position(self, symbol):
        raise NotImplementedError

    # ---- 對帳用 ----

    def fetch_order(self, client_order_id, symbol):
        """
        查一張單現在到底怎麼了。回傳 dict 或 None(交易所查無此單)。

        回傳 None 與「查詢失敗」是**不同**的兩件事:
        None 代表可以確定沒有這張單,查詢失敗要拋例外。
        把查詢失敗當成 None,會讓一張其實已經成交的單被標成 REJECTED。
        """
        raise NotImplementedError

    def fetch_positions(self):
        """交易所那邊現在有哪些部位。拿不到就拋例外,不要回空清單。"""
        raise NotImplementedError


class PaperBroker(Broker):
    """
    模擬盤 broker。

    停損在模擬盤是**存在交易列上、由 position_monitor 執行**的,
    不是交易所那邊的一張掛單。所以 `has_protection()` 檢查的是
    那一列有沒有停損 —— 沒有就代表這個部位沒有虧損上限,
    與實盤「停損單沒掛上去」是同一件事。
    """

    name = "paper"
    is_live = False

    def __init__(self, trading=None, positions=None):
        # 延後 import:這一層不該在模組載入時就把資料庫拉進來
        if trading is None:
            import paper_trading as trading
        if positions is None:
            from database_service import get_open_trade as positions

        self._trading = trading
        self._get_position = positions

    def submit_entry(self, order_request, intent, size_usdt, leverage):
        result = self._trading.create_paper_trade(
            symbol=intent.symbol,
            entry_price=intent.entry,
            signal=intent.direction.value,
            size_usdt=size_usdt,
            stoploss=intent.stop_loss,
            takeprofit=intent.take_profit,
            leverage=leverage,
            source="EXEC",
        )

        if not result.get("success"):
            return FillResult(ok=False, reason=result.get("message"), raw=result)

        trade = result["trade"]
        quantity = (
            trade["position_value"] / trade["entry_price"]
            if trade.get("position_value") and trade.get("entry_price")
            else order_request.quantity
        )

        return FillResult(
            ok=True,
            filled_quantity=quantity,
            average_price=trade["entry_price"],
            exchange_order_id=str(trade.get("id")),
            raw=result,
        )

    def has_protection(self, symbol):
        position = self._get_position(symbol)
        if not position:
            return False
        return position.get("stoploss") is not None

    def close_position(self, symbol, price=None, reason=""):
        if price is None:
            from market_data import get_price
            price = get_price(symbol)

        if price is None:
            return FillResult(ok=False, reason=f"{symbol} 取不到現價,無法平倉")

        result = self._trading.close_paper_trade(symbol, price, reason)

        if not result.get("success"):
            return FillResult(
                ok=False, reason=result.get("message"), raw=result,
            )

        return FillResult(
            ok=True,
            filled_quantity=result["trade"].get("size_usdt") or 0.0,
            average_price=result["trade"].get("exit_price"),
            raw=result,
        )

    def get_position(self, symbol):
        return self._get_position(symbol)

    # ---- 對帳用 ----

    def fetch_order(self, client_order_id, symbol):
        """
        模擬盤沒有訂單簿,只有交易列。所以「這張單怎麼了」要用
        「這個標的現在有沒有倉位」來回答。

        這在模擬盤是夠的:create_paper_trade 要嘛寫入一列、要嘛沒寫,
        沒有部分成交。實盤必須改成真的查交易所訂單。
        """
        position = self._get_position(symbol)

        if position is None:
            return None

        return {
            "state": "protected" if position.get("stoploss") is not None
                     else "filled",
            "filled_quantity": (
                position["position_value"] / position["entry_price"]
                if position.get("position_value") and position.get("entry_price")
                else None
            ),
            "average_price": position.get("entry_price"),
            "exchange_order_id": str(position.get("id")) if position.get("id") else None,
        }

    def fetch_positions(self):
        """
        模擬盤的「交易所部位」就是交易表本身。

        ⚠️ 這代表模擬盤的部位對帳是**拿同一份資料跟自己比**,
        永遠不會發現差異。真正的部位漂移只有實盤才驗得出來。
        這裡回傳它是為了讓對帳流程本身在模擬盤也能跑得通,
        而不是假裝這樣就驗證過了。
        """
        from database_service import get_open_trades
        return get_open_trades()
