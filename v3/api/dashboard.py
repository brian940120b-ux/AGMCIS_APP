"""
Dashboard REST API

WebSocket 負責即時推送，REST 負責首次載入與外部查詢，
兩邊共用同一個 payload 組裝函式，格式保證一致。
"""
import asyncio

from fastapi import APIRouter

from config import WEBSOCKET_INTERVAL
from services.dashboard import get_dashboard_payload

router = APIRouter(prefix="/api", tags=["dashboard"])


async def _payload():
    # 阻塞操作丟到 thread，不卡事件迴圈
    return await asyncio.to_thread(get_dashboard_payload, WEBSOCKET_INTERVAL)


@router.get("/dashboard")
async def dashboard():
    return await _payload()


@router.get("/portfolio")
async def portfolio():
    return (await _payload())["portfolio"]


@router.get("/market_scan")
async def market_scan():
    return (await _payload())["market_scan"]


@router.get("/ai_decisions")
async def ai_decisions():
    return (await _payload())["ai_decisions"]
