"""
BingX 標準合約介面 · 2026-09-10(PHASE 2)—— **尚不可用,原因不在我們**

═══ 查證結果(2026-09-10)═══
Master Prompt 第 6 條要求 Standard 與 Perpetual 明確分離。型別層已經
分離(exchange/types.py 的 MarketType),但**實作不了**,理由是交易所端:

官方文件 BingX-API/BingX-Standard-Contract-doc 的 REST API 只有三個端點,
而且**全部是私有的**:
    GET /openApi/contract/v1/allPosition   查持倉
    GET /openApi/contract/v1/allOrders     查歷史訂單
    GET /openApi/contract/v1/balance       查餘額

**沒有任何公開的 REST 行情或合約清單端點** —— 連「有哪些標的可以交易」
都查不到,更不用說精度、最小下單量、費率。而那些正是訂單層必須知道的
東西(2026-09-09 的教訓:不知道精度就會產生七張全部被拒的單)。

文件並明寫:**"currently in internal testing"**、申請頁面尚未開放。

實測(2026-09-10):
    /openApi/contract/v1/allPosition  → code 100413(需認證)
    /openApi/contract/v1/contracts    → code 100400(端點不存在)
    /openApi/cswap/v1/market/contracts → code 104414

═══ 為什麼還是把這個檔案建起來 ═══
一、**型別層先分離**,避免將來加入時整套帳本要重寫
二、**把限制寫在程式碼裡**,而不是只寫在某份文件裡 —— 下一個看到
    「系統支援 Standard Futures 嗎」的人,執行到這裡會拿到明確答案
三、**不假裝支援**:所有方法都拋 NotSupported 並說明確切原因。
    一個「看起來存在、實際無作用」的東西,比沒有更糟(舊系統教訓四)。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from exchange.base import ExchangeAdapter
from exchange.types import Contract, MarketType, NotSupported, OrderRequest

_REASON = (
    "BingX 標準合約 API 目前無法使用 —— 原因在交易所端,不是本系統:"
    "官方文件只提供三個**私有**端點(allPosition/allOrders/balance),"
    "沒有任何公開的行情或合約清單端點,連可交易標的、數量精度、"
    "最小下單量、費率都查不到;且文件標明 'currently in internal testing'、"
    "申請頁面尚未開放。查證日期 2026-09-10,見 exchange/bingx/standard.py。"
)


class BingXStandard(ExchangeAdapter):
    """BingX 標準合約。**型別已就位,實作待交易所開放。**"""

    name = "bingx-standard"
    market_type = MarketType.STANDARD
    is_live = False

    def contracts(self) -> dict[str, Contract]:
        raise NotSupported(_REASON)

    def mark_prices(self, symbols=None) -> dict[str, float]:
        raise NotSupported(_REASON)

    def last_prices(self, symbols=None) -> dict[str, float]:
        raise NotSupported(_REASON)

    def submit(self, req: OrderRequest):
        raise NotSupported(_REASON)
