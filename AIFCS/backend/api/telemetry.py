"""WebSocket telemetry endpoint (PHASE 7)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from core.logging_config import get_logger
from core.runtime import get_broadcaster, get_engine
from core.simulation_engine import SimulationEngine

router = APIRouter(tags=["telemetry"])
log = get_logger("api.telemetry")


@router.websocket("/ws/simulation")
async def simulation_telemetry(websocket: WebSocket) -> None:
    """Stream simulation state to a dashboard.

    The server pushes; the client is not required to say anything. Whatever a
    client does send is drained and ignored, which is also what keeps this
    coroutine alive to notice a disconnect.
    """
    await websocket.accept()
    broadcaster = get_broadcaster()
    subscriber = await broadcaster.connect(websocket)

    log.info(
        "telemetry client connected",
        extra={"event": "TELEMETRY_CONNECT", "subscribers": broadcaster.subscriber_count},
    )

    try:
        while True:
            # Nothing is expected from the client; this receive is how a
            # disconnect surfaces.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("telemetry socket failed", extra={"event": "TELEMETRY_SOCKET_ERROR"})
    finally:
        broadcaster.disconnect(subscriber)
        log.info(
            "telemetry client disconnected",
            extra={"event": "TELEMETRY_DISCONNECT", "subscribers": broadcaster.subscriber_count},
        )


@router.get("/api/telemetry")
def telemetry_status(engine: SimulationEngine = Depends(get_engine)) -> dict[str, Any]:
    """Broadcaster status: rate, connected clients, frames sent."""
    return get_broadcaster().status()
