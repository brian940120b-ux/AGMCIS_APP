from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import APP_NAME, VERSION

app = FastAPI(
    title=APP_NAME,
    version=VERSION
)

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

from api.summary import router as summary_router
app.include_router(summary_router)

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from api.system_health import router as system_health_router
app.include_router(system_health_router)

from api.portfolio import router as portfolio_router
app.include_router(portfolio_router)

from api.market_scan import router as market_scan_router
app.include_router(market_scan_router)

from api.ai_decisions import router as ai_decisions_router
app.include_router(ai_decisions_router)
