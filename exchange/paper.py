"""
紙上交易介面 · 2026-09-10(PHASE 2)

═══ 為什麼紙上也要走 Adapter ═══
若紙上走一條路、實盤走另一條路,那就是第十二次「兩把尺」——
而且是最貴的一種:紙上的績效會是在一個「跟實盤不同的世界」裡跑出來的。

所以 PaperExchange 與 BingXPerpetual **共用同一份規格來源**
(portfolio.specs)、同一份成本來源(portfolio.costs)、同一個介面。
唯一的差別是 submit() 不送出真單。

═══ 結構性保證 ═══
本類別**沒有任何交易所 client 的 import** —— 不是靠自律,是靠結構。
tests/test_exchange_adapter.py 會斷言這件事。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from exchange.base import ExchangeAdapter
from exchange.types import (Contract, MarketType, OrderRequest, OrderResult,
                            OrderStatus, OrderType)


class PaperExchange(ExchangeAdapter):
    """紙上永續。規格與價格取自真實交易所,但**永不送出訂單**。"""

    name = "paper"
    market_type = MarketType.PERPETUAL
    is_live = False

    def contracts(self) -> dict[str, Contract]:
        from exchange.bingx.perpetual import BingXPerpetual
        # 規格用真的 —— 紙上若用假的規格,測出來的可交易性就是假的
        return BingXPerpetual().contracts()

    def mark_prices(self, symbols=None) -> dict[str, float]:
        from portfolio.live import mark_prices
        return mark_prices(symbols)

    def last_prices(self, symbols=None) -> dict[str, float]:
        from portfolio.live import prices
        return prices(symbols)

    def funding_rates(self, symbol, since_ms, until_ms):
        from portfolio import specs
        return specs.funding_settlements(symbol, since_ms, until_ms)

    def submit(self, req: OrderRequest) -> OrderResult:
        """紙上成交:市價單吃滑點,限價/只掛單不吃滑點。

        滑點方向永遠對自己不利 —— 那是市價單的本質,也是唯一誠實的假設。
        成交價依該幣的價格精度進位,與交易所一致。
        """
        from portfolio import specs
        from portfolio.costs import SLIP_FLOOR_PCT

        spec = self.spec(req.symbol)
        qty = self.round_qty(req.symbol, req.qty)
        if qty <= 0 or qty < spec.min_qty:
            return OrderResult(
                status=OrderStatus.REJECTED, symbol=req.symbol,
                market_type=self.market_type,
                client_order_id=req.client_order_id,
                ordered_qty=req.qty,
                error=f"數量 {req.qty} 低於最小下單量 {spec.min_qty}")

        ref = req.price or self.last_prices([req.symbol]).get(req.symbol)
        if not ref:
            return OrderResult(
                status=OrderStatus.REJECTED, symbol=req.symbol,
                market_type=self.market_type,
                client_order_id=req.client_order_id,
                error="取不到參考價")

        if req.order_type is OrderType.MARKET:
            slip = SLIP_FLOOR_PCT / 100.0
            px = ref * (1 + slip) if req.side.value == "BUY" else \
                ref * (1 - slip)
            fee_pct = spec.taker_fee_pct
        else:
            px = ref                      # 掛單成交在自己的價位,不吃價差
            fee_pct = spec.maker_fee_pct

        px = self.round_price(req.symbol, px)
        notional = qty * px
        if notional < spec.min_notional:
            return OrderResult(
                status=OrderStatus.REJECTED, symbol=req.symbol,
                market_type=self.market_type,
                client_order_id=req.client_order_id, ordered_qty=req.qty,
                error=f"名目 {notional:.2f} 低於下限 {spec.min_notional}")

        return OrderResult(
            status=OrderStatus.FILLED, symbol=req.symbol,
            market_type=self.market_type,
            client_order_id=req.client_order_id,
            ordered_qty=qty, filled_qty=qty, avg_price=px,
            fee=notional * fee_pct / 100.0,
            raw={"ref_price": ref, "order_type": req.order_type.value})
