import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import APP_NAME, VERSION
from ws.routes import router as websocket_router, broadcast_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 單一推播任務，所有 websocket 連線共用同一份 payload
    task = asyncio.create_task(broadcast_loop())
    try:
        yield
    finally:
        task.cancel()

app = FastAPI(title=APP_NAME, version=VERSION, lifespan=lifespan)

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/health")
def health():
    return {
        "app": APP_NAME,
        "version": VERSION,
        "status": "running"
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={}
    )


app.include_router(websocket_router)
