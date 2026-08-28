"""
WebSocket 連線管理 + 單一廣播器

舊版是「每個連線各跑一個迴圈，每輪 broadcast 給所有人」，
N 個分頁會讓每輪送出 N x N 則訊息，而且每個連線都各打一次交易所 API。

改成整個 app 只有一個 broadcaster task：
定時算一次 payload，推給所有連線。
"""
import asyncio
import logging
from typing import List

from fastapi import WebSocket

logger = logging.getLogger("agmcis.v3.ws")


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self._broadcaster: asyncio.Task | None = None

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def send_personal(self, websocket: WebSocket, message: dict):
        try:
            await websocket.send_json(message)
        except Exception:
            self.disconnect(websocket)

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)

    async def _run(self, build_payload, interval: int):
        """
        build_payload 是同步且會阻塞的（DB + 交易所 API），
        所以丟到 thread 執行，避免卡住整個事件迴圈。
        """
        while True:
            try:
                if self.active_connections:
                    payload = await asyncio.to_thread(build_payload)
                    await self.broadcast(payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("broadcaster error: %s", exc)

            await asyncio.sleep(interval)

    def start_broadcaster(self, build_payload, interval: int):
        if self._broadcaster is None or self._broadcaster.done():
            self._broadcaster = asyncio.create_task(
                self._run(build_payload, interval)
            )
        return self._broadcaster

    async def stop_broadcaster(self):
        if self._broadcaster is None:
            return

        self._broadcaster.cancel()
        try:
            await self._broadcaster
        except asyncio.CancelledError:
            pass
        finally:
            self._broadcaster = None


manager = ConnectionManager()
