import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from config import WEBSOCKET_INTERVAL
from services.dashboard import get_dashboard_payload
from ws.manager import manager

router = APIRouter()


async def build_payload():
    # get_dashboard_payload 會做 DB / 交易所同步查詢，丟到 thread 避免卡住 event loop
    return await asyncio.to_thread(get_dashboard_payload)


async def broadcast_loop():
    while True:
        if manager.active_connections:
            await manager.broadcast(await build_payload())
        await asyncio.sleep(WEBSOCKET_INTERVAL)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)

    try:
        # 新連線先推一次，不用等下一輪 interval
        await websocket.send_json(await build_payload())

        while True:
            await websocket.receive_text()

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
