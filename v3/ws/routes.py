import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from services.dashboard import get_cached_payload, get_dashboard_payload
from config import WEBSOCKET_INTERVAL
from ws.manager import manager

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)

    try:
        # 連上就先送一份，畫面不用等下一輪廣播
        snapshot = get_cached_payload()
        if snapshot is None:
            snapshot = await asyncio.to_thread(
                get_dashboard_payload, WEBSOCKET_INTERVAL
            )
        await manager.send_personal(websocket, snapshot)

        # 後續資料由 broadcaster 統一推送，這裡只等待斷線
        while True:
            await websocket.receive_text()

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
