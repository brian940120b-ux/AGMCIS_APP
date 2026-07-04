import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from ws.manager import manager
from config import WEBSOCKET_INTERVAL
from services.dashboard import get_dashboard_payload

router = APIRouter()

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)

    try:
        while True:
            await manager.broadcast(get_dashboard_payload())
            await asyncio.sleep(WEBSOCKET_INTERVAL)

    except WebSocketDisconnect:
        manager.disconnect(websocket)
