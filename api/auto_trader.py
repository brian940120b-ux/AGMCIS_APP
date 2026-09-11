"""
自動交易的 HTTP 端點。

Phase 0.5 的修正:
  1. 改為 POST。原本是 GET —— 一個瀏覽器預抓、一個爬蟲、一次誤點都可能開倉,
     而 GET 依定義不應該變更狀態。
  2. 金鑰驗證由 main.py 掛在 router 上(原本完全公開)。
  3. 風控 gate 已經在 run_auto_trader() 內部,這個端點無法繞過。
"""
from fastapi import APIRouter

from auto_trader import run_auto_trader

router = APIRouter()


@router.post("/api/auto_trader")
def api_auto_trader():
    return run_auto_trader()
