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
from direction import is_long, is_short
import risk_limits
from market_data import get_price
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
from api.overview import router as overview_router
from api.equity import router as equity_router
from api.journal import router as journal_router
from api.leaderboard import router as leaderboard_router
from api.logger_health import router as logger_health_router
from api.market_scan import router as market_scan_router
from api.performance import router as performance_router
from api.portfolio import router as portfolio_router
from api.scheduler_status import router as scheduler_status_router
from api.stats import router as stats_router
from api.system_health import router as system_health_router
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
):
    app.include_router(_router, dependencies=PROTECTED)

# /health 刻意**不掛 API 金鑰**。健康檢查是給外部監控用的 ——
# uptime 監測、負載平衡器、systemd 看門狗都不會帶金鑰。
# 一個需要金鑰才打得到的健康端點等於沒有健康端點。
# 它只回狀態不回內容,錯誤細節留在 /api/system_health(需要金鑰)與 log。
app.include_router(health_router)

app.mount("/static", StaticFiles(directory="static"), name="static")

def tr(rows):
    """持倉 / 平倉表格。槓桿一律取交易本身的值,不再寫死 3x。"""
    if not rows:
        return "<tr><td colspan=14>目前沒有資料</td></tr>"

    out = []

    for t in rows:
        pnl = t.get("pnl_usdt")
        symbol = t.get("symbol")
        signal = t.get("signal")
        is_open = t.get("status") == "OPEN"
        current = get_price(symbol) if is_open else t.get("exit_price")
        entry = float(t.get("entry_price") or 0)
        size = float(t.get("size_usdt") or 0)
        lev = float(t.get("leverage") or 1)

        if current and entry > 0:
            if is_long(signal):
                change = (float(current) - entry) / entry
            elif is_short(signal):
                change = (entry - float(current)) / entry
            else:
                change = None

            if change is None:
                roi = upnl = "-"
            else:
                roi = round(change * lev * 100, 2)
                upnl = round(size * change * lev, 2)
        else:
            roi = upnl = "-"

        pnlc = "pos" if (pnl or 0) > 0 else "neg" if (pnl or 0) < 0 else ""
        notional = round(size * lev, 2)

        out.append(
            f"<tr><td>{symbol or '-'}</td><td>{signal or '-'}</td>"
            f"<td>{lev:g}x</td><td>{notional} USDT</td>"
            f"<td>{t.get('entry_price', '-')}</td><td>{current if current else '-'}</td>"
            f"<td>{t.get('exit_price', '-')}</td><td>{t.get('stoploss') or '⚠️ 無'}</td>"
            f"<td>{t.get('takeprofit', '-')}</td>"
            f"<td class='{pnlc}'>{roi}%</td><td class='{pnlc}'>{upnl} USDT</td>"
            f"<td class='{pnlc}'>{pnl if pnl is not None else '-'}</td>"
            f"<td>{t.get('status', '-')}</td>"
            f"<td>{t.get('close_reason') or t.get('opened_at', '-')}</td></tr>"
        )

    return "".join(out)
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
    # 金鑰從 query string 帶進來時,驗證後存進 HttpOnly cookie 並導回乾淨網址,
    # 讓金鑰不留在瀏覽歷史與 Nginx access log 裡。
    key = request.query_params.get("key")
    if key and key_is_valid(key):
        return redirect_with_session("/dashboard", key, request)

    if not is_authenticated(request):
        return HTMLResponse(LOGIN_PAGE, status_code=401)

    a=get_account();o=get_open_trades();c=get_closed_trades();w=a.get("wins",0);l=a.get("losses",0);n=a.get("trades",0);wr=round(w/n*100,2) if n else 0
    net=round(sum((t.get("pnl_usdt") or 0) for t in c),2)
    netc="pos" if net>=0 else "neg"
    max_lev=risk_limits.MAX_LEVERAGE
    return f"""<html><head><meta charset='utf-8'><title>AGMCIS Dashboard</title><link rel='stylesheet' href='/static/css/dashboard.css'></head><body>
<h1>AGMCIS Dashboard Lite</h1><p class='muted'>Top50 掃描、模擬交易、TP/SL、Telegram 都在背景服務運作。API 即時更新模式。</p>
<p><a href='/' style='color:#38bdf8'>← 回首頁</a> · <a href='/transparency' style='color:#38bdf8'>→ 透明度面板</a>(Agent 投票、訂單狀態、對帳差異、成本、規格校準)</p><p class='muted'>最後更新：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
<div class='grid'><div class='card'>帳戶資金<br><b id='balance'>{a.get('balance')} USDT</b></div><div class='card'>總交易<br><b id='trades'>{n}</b></div><div class='card'>勝率<br><b id='win_rate'>{wr}%</b></div><div class='card'>目前持倉<br><b id='open_count'>{len(o)}</b></div><div class='card'>淨損益<br><b class='{netc}'>{net} USDT</b></div><div class='card'>槓桿上限<br><b>{max_lev:g}x</b></div><div class='card'>總浮盈虧<br><b id='total_open_upnl'>0 USDT</b></div><div class='card'>風險等級<br><b id='risk_level'>LOW</b></div><div class='card'>系統狀態<br><b id='system_status'>-</b></div><div class='card'>最佳交易<br><b id='best_trade'>0</b></div><div class='card'>最差交易<br><b id='worst_trade'>0</b></div><div class='card'>Profit Factor<br><b id='profit_factor'>0</b></div><div class='card'>已平倉<br><b id='total_closed_trades'>0</b></div></div>
<h2>績效分析</h2><div class="grid"><div class="card">💰 已實現收益<br><b id="total_realized">0</b></div><div class="card">🔥 最大連勝<br><b id="max_win_streak">0</b></div><div class="card">❄️ 最大連敗<br><b id="max_loss_streak">0</b></div><div class="card">📊 已平倉交易<br><b id="analytics_closed_trades">0</b></div></div><h2>目前持倉</h2><table id="open_positions_table"><tr><th>幣種</th><th>方向</th><th>槓桿</th><th>倉位價值</th><th>進場</th><th>現價</th><th>出場</th><th>停損</th><th>停利</th><th>ROI</th><th>UPNL</th><th>已實現</th><th>狀態</th><th>時間/原因</th></tr>{tr(o)}</table>
<h2>系統健康監控</h2><div class="grid"><div class="card">FastAPI<br><b id="health_api">🟢 OK</b></div><div class="card">Risk Timer<br><b id="health_risk">🟢 ON</b></div><div class="card">Daily Report<br><b id="health_report">🟢 ON</b></div><div class="card">Optimizer<br><b id="health_optimizer">🟢 ON</b></div><div class="card">最後更新<br><b id="last_update">-</b></div><div class="card">Uptime<br><b id="uptime">-</b></div></div><h2>資金曲線</h2><div class="card"><canvas id="equityChart" height="120"></canvas></div><h2>排行榜</h2><div class="grid"><div class="card"><h3>🏆 Top Winners</h3><div id="top_winners">Loading...</div></div><div class="card"><h3>💀 Top Losers</h3><div id="top_losers">Loading...</div></div></div><h2>持倉總覽</h2><div class="grid"><div class="card">🟢 獲利持倉<br><b id="profit_positions">0</b></div><div class="card">🔴 虧損持倉<br><b id="loss_positions">0</b></div><div class="card">⚪ 打平持倉<br><b id="flat_positions">0</b></div><div class="card">📈 最大浮盈<br><b id="max_profit_position">-</b></div><div class="card">📉 最大浮虧<br><b id="max_loss_position">-</b></div></div><h2>目前持倉排行</h2><div class="grid"><div class="card"><h3>🔥 最佳持倉</h3><div id="best_positions">Loading...</div></div><div class="card"><h3>⚠️ 最差持倉</h3><div id="worst_positions">Loading...</div></div><div class="card"><h3>🚨 最接近停損</h3><div id="nearest_sl">Loading...</div></div><div class="card"><h3>🎯 最接近停利</h3><div id="nearest_tp">Loading...</div></div></div>


<h2>🟢 System Health</h2>
<div class="grid">
<div class="card"><div id="system_health">Loading Health...</div></div>
</div>

<h2>💰 Portfolio Summary</h2>

<div class="grid">
<div class="card">
<div id="portfolio_summary">Loading Portfolio...</div>
</div>
</div>

<h2>⚙️ Scheduler Status</h2>
<div class="grid"><div class="card"><div id="scheduler_status">Loading Scheduler...</div></div></div>\n<h2>🔥 Top 3 Opportunities</h2><div class="grid"><div class="card"><div id="top_opportunities">Loading Top 3...</div></div></div>
<h2>📈 Market Scanner Top 10</h2><div class="grid"><div class="card"><div id="market_scan">Loading Market Scanner...</div></div></div>\n<h2>🤖 AI Decision Center</h2><div class="grid"><div class="card"><div id="ai_decisions">Loading AI...</div></div></div>
<h2>📋 System Logger Health</h2><div class="grid"><div class="card"><div id="logger_health">Loading logs...</div></div></div>\n<h2>最近平倉</h2><table><tr><th>幣種</th><th>方向</th><th>槓桿</th><th>倉位價值</th><th>進場</th><th>現價</th><th>出場</th><th>停損</th><th>停利</th><th>ROI</th><th>UPNL</th><th>已實現</th><th>狀態</th><th>時間/原因</th></tr>{tr(c[-10:])}</table>
<p class='muted'>服務：agmcis / agmcis-opportunity / agmcis-position / agmcis-report</p><script src='https://cdn.jsdelivr.net/npm/chart.js'></script><script src='/static/js/api.js?v=76'></script><script src='/static/js/dashboard.js?v=v88_health'></script></body></html>"""


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


@app.get("/v1", response_class=HTMLResponse)
def dashboard_v1(request: Request):
    key = request.query_params.get("key")
    if key and key_is_valid(key):
        return redirect_with_session("/v1", key, request)

    if not is_authenticated(request):
        return HTMLResponse(LOGIN_PAGE, status_code=401)

    return templates.TemplateResponse(request=request, name="dashboard.html", context={})
