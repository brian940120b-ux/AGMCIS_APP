import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from ws.manager import manager
from config import WEBSOCKET_INTERVAL

router = APIRouter()

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)

    try:
        while True:
            await manager.broadcast({
                "type": "heartbeat",
                "status": "running"
            })
            await asyncio.sleep(WEBSOCKET_INTERVAL)

    except WebSocketDisconnect:
        manager.disconnect(websocket)
