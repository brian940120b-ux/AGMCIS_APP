"""
BingX 永續合約介面 · 2026-09-10(PHASE 2)

═══ 這一支不重新實作任何東西 ═══
Master Prompt 第 8 條:「如果現有專案已有類似模組:不要重複建立。
應該 REFACTOR EXISTING CODE。」

本系統已經有:
    portfolio/specs.py    合約規格、逐幣費率、實際結算資金費
    portfolio/live.py     標記價(premiumIndex)
    market_data/          行情、K 線、累積式快取
這一支**只是把它們包成統一介面**,一行邏輯都不搬過來 ——
搬過來就是憲法鐵則一講的「第二份實作」,而舊系統為此付過十一次代價。

═══ 私有端點的狀態 ═══
下單、持倉、餘額**尚未實作**,因為:
一、需要 API 金鑰(執政官尚未提供,而那是需要人工確認的事)
二、`LIVE_ENABLED = False` 是原始碼常數,實盤資格契約只過 2/8
所以這裡明確拋 NotSupported,**不給假的回傳值**。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from exchange.base import ExchangeAdapter
from exchange.types import (Contract, MarketType, NotSupported, OrderRequest,
                            OrderResult)


class BingXPerpetual(ExchangeAdapter):
    """BingX USDⓢ-M 永續合約。公開行情可用,私有端點未實作。"""

    name = "bingx-perpetual"
    market_type = MarketType.PERPETUAL
    is_live = False          # 私有端點未實作,不可能送出真單

    def contracts(self) -> dict[str, Contract]:
        """合約規格 —— 委派給 portfolio.specs(它每日由 daily.py 更新)。"""
        from portfolio import specs
        out = {}
        for sym, sp in specs._load().items():
            out[sym] = Contract(
                symbol=sym,
                market_type=MarketType.PERPETUAL,
                quantity_precision=sp["quantity_precision"],
                price_precision=sp["price_precision"],
                min_qty=sp["min_qty"],
                min_notional=sp["min_notional"],
                taker_fee_pct=sp["taker_fee"] * 100.0,
                maker_fee_pct=sp["maker_fee"] * 100.0,
                contract_size=sp.get("size", 1.0),
                tradable=sp.get("status") == 1,
                expiry=None,                 # 永續無到期
            )
        return out

    def mark_prices(self, symbols=None) -> dict[str, float]:
        """標記價 —— 委派給 portfolio.live(它已處理快取與降級標記)。"""
        from portfolio.live import mark_prices
        return mark_prices(symbols)

    def last_prices(self, symbols=None) -> dict[str, float]:
        """最新成交價 —— 委派給 portfolio.live(串流優先、REST 退路)。"""
        from portfolio.live import prices
        return prices(symbols)

    def funding_rates(self, symbol: str, since_ms: int,
                      until_ms: int) -> list[dict]:
        """區間內**實際結算**的資金費 —— 委派給 portfolio.specs。

        注意它會在快取不完整時拋 SpecMissing,而那是刻意的:
        用不完整的資料記帳會靜靜少收資金費、美化績效。
        """
        from portfolio import specs
        return specs.funding_settlements(symbol, since_ms, until_ms)

    # ── 私有端點:尚未實作,明確拒絕 ────────────────────────
    def submit(self, req: OrderRequest) -> OrderResult:
        raise NotSupported(
            "BingX 永續實盤下單尚未實作。缺三樣東西:"
            "(1) API 金鑰(需執政官提供,涉及真實資金)"
            "(2) HMAC 簽名 / RateLimiter / 錯誤碼映射(PHASE 3)"
            "(3) 實盤三道鎖:LIVE_ENABLED 原始碼常數、"
            "實盤資格契約八條(目前 2/8)、人工簽署")

    def positions(self) -> list[dict]:
        raise NotSupported("BingX 持倉查詢需要 API 金鑰(PHASE 3)")

    def balance(self) -> dict:
        raise NotSupported("BingX 餘額查詢需要 API 金鑰(PHASE 3)")

    def cancel_all(self, symbol: str | None = None) -> int:
        raise NotSupported("BingX 撤單需要 API 金鑰(PHASE 3)")
