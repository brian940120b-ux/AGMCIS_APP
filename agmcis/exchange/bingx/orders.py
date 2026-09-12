"""
BingX 下單(Master Prompt 第八節的 orders.py)。

## 這是整個系統唯一會送出訂單的地方

所以它有兩條規則,兩條都寫在 client 層而不是這裡:

**一、寫入失敗不重試。** 送出訂單後連線斷掉,交易所可能已經收到了。
重送是重複開倉最常見的來源。`is_write=True` 讓 error_policy 走
ACTION_RECONCILE:拋一個「狀態不明」的例外,由對帳處理。

**二、這一層不做任何判斷。** 沒有精度驗證、沒有風控、沒有狀態機。
那些在 Trading Rules Engine(第十七節)、Risk Engine(第十九節)與
Execution Engine 裡,而且順序是固定的:

    Risk Engine -> Trading Rules -> Execution Engine -> 這裡

第三十二節:AI / LLM 不得直接呼叫這裡。
"""
from agmcis.core.enums import MarketType, OrderSide, OrderType


class OrdersMixin:
    """BingXAdapter 的一部分。需要 client 層的 _call。"""

    def create_order(self, symbol, side, quantity, order_type=OrderType.MARKET,
                     price=None, market_type=MarketType.PERPETUAL,
                     client_order_id=None, reduce_only=False, params=None):
        """
        ⚠️ 不可直接呼叫 —— 見模組說明。這裡沒有精度驗證、沒有風控、
        沒有狀態機、沒有對帳。正確的入口是 Execution Engine。
        """
        market_symbol = self.to_market_symbol(symbol, market_type)
        side = OrderSide.parse(side, OrderSide.BUY)
        order_type = OrderType.parse(order_type, OrderType.MARKET)

        request = dict(params or {})
        if client_order_id:
            # 冪等鍵。重送同一個 clientOrderId,交易所會拒絕第二次 ——
            # 那是「狀態不明時不可重送」之外的第二道保護。
            request["clientOrderId"] = client_order_id
        if reduce_only:
            request["reduceOnly"] = True

        return self._call(
            "create_order", market_symbol, order_type.value, side.value,
            quantity, price, request, market_type=market_type, is_write=True,
        )

    def cancel_order(self, order_id, symbol, market_type=MarketType.PERPETUAL):
        market_symbol = self.to_market_symbol(symbol, market_type)
        return self._call(
            "cancel_order", order_id, market_symbol,
            market_type=market_type, is_write=True,
        )

    def get_order(self, order_id, symbol, market_type=MarketType.PERPETUAL):
        """查詢是唯讀的,所以走一般重試。"""
        market_symbol = self.to_market_symbol(symbol, market_type)
        return self._call(
            "fetch_order", order_id, market_symbol, market_type=market_type
        )
