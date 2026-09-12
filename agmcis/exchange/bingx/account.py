"""
BingX 帳戶與合約設定(Master Prompt 第八節的 account.py)。

## 這個檔案裡有三個會改變帳戶狀態的方法

`set_leverage` 與 `set_margin_mode` 不是查詢,它們會改交易所上的設定,
而且**改了就影響所有既有部位的強平價**。它們走 `is_write=True`,
所以逾時不會重試(重試一個可能已經生效的設定變更沒有意義)。

第十九節:風控是 hard gate。這一層不做判斷,它只執行 —— 要不要改槓桿
由 Risk Engine 決定。
"""
from agmcis.core.enums import MarketType


class AccountMixin:
    """BingXAdapter 的一部分。需要 client 層的 _call。"""

    # ---------------- 查詢 ----------------

    def get_balance(self):
        return self._call("fetch_balance")

    def get_leverage(self, symbol, market_type=MarketType.PERPETUAL):
        market_symbol = self.to_market_symbol(symbol, market_type)
        return self._call("fetch_leverage", market_symbol, market_type=market_type)

    def get_position_mode(self, symbol=None, market_type=MarketType.PERPETUAL):
        """One-Way 還是 Hedge。下單的 positionSide 參數取決於這個。"""
        market_symbol = (
            self.to_market_symbol(symbol, market_type) if symbol else None
        )
        return self._call(
            "fetch_position_mode", market_symbol, market_type=market_type
        )

    # ---------------- 變更(會影響既有部位) ----------------

    def set_leverage(self, leverage, symbol, market_type=MarketType.PERPETUAL):
        """
        ⚠️ 這會改變帳戶設定,而且**改了就影響既有部位的強平價**。
        要不要改由 Risk Engine 決定(第十九節),這一層只執行。
        """
        market_symbol = self.to_market_symbol(symbol, market_type)
        return self._call(
            "set_leverage", leverage, market_symbol,
            market_type=market_type, is_write=True,
        )

    def set_margin_mode(self, margin_mode, symbol, market_type=MarketType.PERPETUAL):
        """⚠️ 這會改變帳戶設定。逐倉與全倉的強平邏輯完全不同。"""
        market_symbol = self.to_market_symbol(symbol, market_type)
        return self._call(
            "set_margin_mode", margin_mode, market_symbol,
            market_type=market_type, is_write=True,
        )
