from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from api.dashboard import router as api_router
from config import APP_NAME, VERSION, WEBSOCKET_INTERVAL
from services.dashboard import build_dashboard_payload, get_cached_payload
from ws.manager import manager
from ws.routes import router as websocket_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 整個 app 只有一個廣播器，不是每條連線各跑一個
    manager.start_broadcaster(build_dashboard_payload, WEBSOCKET_INTERVAL)
    yield
    await manager.stop_broadcaster()


app = FastAPI(title=APP_NAME, version=VERSION, lifespan=lifespan)

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(api_router)
app.include_router(websocket_router)


@app.get("/health")
def health():
    cached = get_cached_payload()

    return {
        "app": APP_NAME,
        "version": VERSION,
        "status": "running",
        "websocket_clients": len(manager.active_connections),
        "data": (cached or {}).get("status", {"state": "starting", "errors": []}),
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"app_name": APP_NAME, "version": VERSION},
    )
