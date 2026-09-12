"""
AGMCIS v1 Dashboard + API(生產中的入口)。

Phase 0.5 的修正:
  1. 所有 /api/* 加上金鑰驗證 —— 原本完全公開,帳戶餘額與交易紀錄任何人可讀。
  2. Dashboard 登入改走 HttpOnly cookie,金鑰不再留在網址列與 access log。
  3. 刪掉 6 個被 api/ router 遮蔽的死碼 endpoint
     (/api/dashboard /api/equity_curve /api/stats /api/leaderboard
      /api/analytics_pro /api/journal)。
     它們定義在 router 註冊之後,FastAPI 會發出 Duplicate Operation ID 警告,
     實際提供服務的一直是 router 版本 —— 改這裡的版本完全沒有效果。
  4. 持倉表格改用每筆交易的真實槓桿,不再一律顯示 3x。
"""
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from database_service import get_account, get_closed_trades, get_open_trades
import risk_limits
from web_auth import (
    LOGIN_PAGE,
    is_authenticated,
    redirect_with_session,
    key_is_valid,
    require_api_key,
)

START_TIME = time.time()

@asynccontextmanager
async def lifespan(_app):
    """
    WebSocket 行情(第四十九節)。預設關閉,由 WEBSOCKET_ENABLED 打開。

    啟動失敗**不會**讓 API 起不來:WebSocket 是加速,不是必要條件。
    行情走 REST 會慢一點,但系統照樣運作,而 /health 會顯示它的狀態。
    """
    try:
        from agmcis.exchange.bingx.stream import start_stream
        start_stream()
    except Exception:
        logging.getLogger("agmcis").exception(
            "WebSocket 行情啟動失敗,行情改走 REST",
        )

    yield

    try:
        from agmcis.exchange.bingx.stream import stop_stream
        stop_stream()
    except Exception:
        pass


app = FastAPI(title="AGMCIS", version="1.0.0", lifespan=lifespan)

templates = Jinja2Templates(directory="templates")

# 每個 API router 都掛上金鑰驗證。新增 router 時務必一併加上 dependencies。
PROTECTED = [Depends(require_api_key)]

from api.ai import router as ai_router
from api.analytics import router as analytics_router
from api.auto_trader import router as auto_trader_router
from api.dashboard import router as dashboard_router
from api.health import router as health_router
from api.jobs import router as jobs_router
from api.live_confirmation import router as live_confirmation_router
from api.overview import router as overview_router
from api.equity import router as equity_router
from api.journal import router as journal_router
from api.leaderboard import router as leaderboard_router
from api.logger_health import router as logger_health_router
from api.market_scan import router as market_scan_router
from api.performance import router as performance_router
from api.performance_dashboard import router as performance_dashboard_router
from api.portfolio import router as portfolio_router
from api.scheduler_status import router as scheduler_status_router
from api.stats import router as stats_router
from api.system_health import router as system_health_router
from api.trade_panel import router as trade_panel_router
from api.transparency import router as transparency_router

for _router in (
    portfolio_router,
    performance_router,
    journal_router,
    stats_router,
    leaderboard_router,
    analytics_router,
    equity_router,
    dashboard_router,
    ai_router,
    logger_health_router,
    market_scan_router,
    auto_trader_router,
    scheduler_status_router,
    system_health_router,
    transparency_router,
    overview_router,
    jobs_router,
    trade_panel_router,
    performance_dashboard_router,
    live_confirmation_router,
):
    app.include_router(_router, dependencies=PROTECTED)

# /health 刻意**不掛 API 金鑰**。健康檢查是給外部監控用的 ——
# uptime 監測、負載平衡器、systemd 看門狗都不會帶金鑰。
# 一個需要金鑰才打得到的健康端點等於沒有健康端點。
# 它只回狀態不回內容,錯誤細節留在 /api/system_health(需要金鑰)與 log。
app.include_router(health_router)

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    """
    第一百零五節的首頁:狀態、模式、Agent、TOP 機會、點進去看 WHY。

    完整儀表板移到 /dashboard —— 它沒有被刪掉,只是不再是第一眼。
    第一眼要回答的是「系統現在健康嗎、是不是真錢、有沒有機會」,
    而不是三十個數字。
    """
    key = request.query_params.get("key")
    if key and key_is_valid(key):
        return redirect_with_session("/", key, request)

    if not is_authenticated(request):
        return HTMLResponse(LOGIN_PAGE, status_code=401)

    return templates.TemplateResponse(
        request=request, name="home.html", context={},
    )


@app.get("/dashboard", response_class=HTMLResponse)
def home(request: Request):
    """
    Dashboard Lite(第九十五節)。

    這個 handler 原本自己抓現價、自己算 ROI 與未實現損益、自己組一個
    二十行的 HTML f-string。三件事在同一個函式裡,而且「算損益」那一份
    是系統裡的**第二份公式** —— 第一份在 agmcis/core/models.py。
    兩份公式只會有一份被修到,而那種 bug 要等到「畫面上的數字跟
    Telegram 通知對不起來」才會被發現。

    現在資料在 api/dashboard_rows.py(損益走 Position 的方法),
    版面在 templates/dashboard_lite.html,這裡只負責把兩邊接起來。
    """
    # 金鑰從 query string 帶進來時,驗證後存進 HttpOnly cookie 並導回乾淨網址,
    # 讓金鑰不留在瀏覽歷史與 Nginx access log 裡。
    key = request.query_params.get("key")
    if key and key_is_valid(key):
        return redirect_with_session("/dashboard", key, request)

    if not is_authenticated(request):
        return HTMLResponse(LOGIN_PAGE, status_code=401)

    return templates.TemplateResponse(
        request=request, name="dashboard_lite.html",
        context=dashboard_context(),
    )


def dashboard_context():
    """
    Dashboard Lite 要顯示的東西。純資料,所以測試不用解析 HTML。
    """
    from api.dashboard_rows import build_rows

    account = get_account()
    open_trades = get_open_trades()
    closed_trades = get_closed_trades()

    total = account.get("trades", 0)
    wins = account.get("wins", 0)

    net = round(sum((t.get("pnl_usdt") or 0) for t in closed_trades), 2)
    open_rows = build_rows(open_trades)

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "balance": account.get("balance"),
        "total_trades": total,
        "win_rate": round(wins / total * 100, 2) if total else 0,
        "net_pnl": net,
        "net_sign": "pos" if net >= 0 else "neg",
        "max_leverage": f"{risk_limits.MAX_LEVERAGE:g}",
        "open_rows": open_rows,
        # 最近十筆。整個歷史放進一張同步渲染的表,會讓這一頁隨著
        # 交易數量線性變慢。
        "closed_rows": build_rows(closed_trades[-10:]),
        # 沒有停損的部位排在所有數字之前 —— 它是這一頁上唯一需要
        # **立刻**行動的東西。
        "naked_count": sum(1 for row in open_rows if row["naked"]),
    }


@app.get("/transparency", response_class=HTMLResponse)
def transparency(request: Request):
    """
    透明度面板:Agent 投票、訂單狀態、對帳差異、成本、規格校準。

    Phase 9-13 加的機制到目前為止只在 log 裡。看不到就等於不存在 ——
    而且你不會知道它是「沒有問題」還是「壞掉了所以什麼都沒回報」。
    """
    key = request.query_params.get("key")
    if key and key_is_valid(key):
        return redirect_with_session("/transparency", key, request)

    if not is_authenticated(request):
        return HTMLResponse(LOGIN_PAGE, status_code=401)

    return templates.TemplateResponse(
        request=request, name="transparency.html", context={},
    )


@app.get("/modes", response_class=HTMLResponse)
def trading_modes(request: Request):
    """
    交易模式與 LIVE 確認(第九十一 / 九十二節)。

    四種模式 MANUAL / PAPER / TEST / LIVE 清楚顯示,而且看得到
    「為什麼是這一個」—— 模式是從三個設定推導的,不是另外存的值。

    PAPER → LIVE 的七項確認也在這裡。它**不會讓系統開始下實單**:
    產生的確認檔只是 LIVE SAFETY GATE 其中一項檢查,而模擬盤筆數、
    天數、上線前檢查那幾項不是簽名就能通過的。而且實單程式碼存在時
    網頁精靈會直接拒絕 —— 那一項要在終端機逐檔讀過原始碼才簽得下去
    (第七十八節)。最壞的情況是磁碟上多了一個檔案。
    """
    key = request.query_params.get("key")
    if key and key_is_valid(key):
        return redirect_with_session("/modes", key, request)

    if not is_authenticated(request):
        return HTMLResponse(LOGIN_PAGE, status_code=401)

    return templates.TemplateResponse(
        request=request, name="modes.html", context={},
    )


@app.get("/trading", response_class=HTMLResponse)
def trading_panel(request: Request):
    """
    交易面板:開倉預覽(第八十七節)、決策鏈(第六十三節)、
    績效四塊(第六十一節)。

    **這一頁不會下單。** 它走與自動交易相同的鏈,但在 Execution Engine
    之前停下來 —— 理由與第九十二節不做 LIVE 網頁開關相同:一個會下單的
    網頁按鈕表達不了「這組數字是三十秒前算的」。
    """
    key = request.query_params.get("key")
    if key and key_is_valid(key):
        return redirect_with_session("/trading", key, request)

    if not is_authenticated(request):
        return HTMLResponse(LOGIN_PAGE, status_code=401)

    return templates.TemplateResponse(
        request=request, name="trading.html", context={},
    )


@app.get("/v1", response_class=HTMLResponse)
def dashboard_v1(request: Request):
    key = request.query_params.get("key")
    if key and key_is_valid(key):
        return redirect_with_session("/v1", key, request)

    if not is_authenticated(request):
        return HTMLResponse(LOGIN_PAGE, status_code=401)

    return templates.TemplateResponse(request=request, name="dashboard.html", context={})
