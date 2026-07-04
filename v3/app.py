from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import APP_NAME, VERSION

from ws.routes import router as websocket_router
app = FastAPI(title=APP_NAME, version=VERSION)

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/health")
def health():
    return {
        "app": APP_NAME,
        "version": VERSION,
        "status": "running"
    }

from fastapi import Request
from fastapi.responses import HTMLResponse

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={}
    )

app.include_router(websocket_router)

# WebSocket Router
from ws.routes import router as websocket_router
app.include_router(websocket_router)

# Direct WebSocket Route
import asyncio
from fastapi import WebSocket, WebSocketDisconnect

@app.websocket("/ws")
async def ws_direct(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json({
                "type": "heartbeat",
                "status": "running"
            })
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass
