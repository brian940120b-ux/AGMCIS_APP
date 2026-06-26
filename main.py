from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from database_service import get_account,get_open_trades,get_closed_trades
from market_data import get_price
from datetime import datetime
import time
import os
from risk_control import get_risk_control_status
START_TIME = time.time()
app=FastAPI()
from api.portfolio import router as portfolio_router
app.include_router(portfolio_router)
from api.performance import router as performance_router
app.include_router(performance_router)
from api.journal import router as journal_router
app.include_router(journal_router)
from api.stats import router as stats_router
app.include_router(stats_router)
from api.leaderboard import router as leaderboard_router
app.include_router(leaderboard_router)
app.mount("/static", StaticFiles(directory="static"), name="static")
def tr(x):
    if not x:return "<tr><td colspan=9>目前沒有資料</td></tr>"
    s=""
    for t in x:
        pnl=t.get("pnl_usdt")
        current=get_price(t.get("symbol")) if t.get("status")=="OPEN" else t.get("exit_price")
        entry=float(t.get("entry_price") or 0)
        size=float(t.get("size_usdt") or 0)
        lev=3
        if current and entry:
            raw=((float(current)-entry)/entry*100) if t.get("signal")=="做多" else ((entry-float(current))/entry*100)
            roi=round(raw*lev,2)
            upnl=round(size*roi/100,2)
        else:
            roi="-";upnl="-"
        pnlc="pos" if (pnl or 0)>0 else "neg" if (pnl or 0)<0 else ""
        s+=f"<tr><td>{t.get('symbol','-')}</td><td>{t.get('signal','-')}</td><td>3x</td><td>{round((t.get('size_usdt') or 0)*3,2)} USDT</td><td>{t.get('entry_price','-')}</td><td>{current if current else '-'}</td><td>{t.get('exit_price','-')}</td><td>{t.get('stoploss','-')}</td><td>{t.get('takeprofit','-')}</td><td class='{pnlc}'>{roi}%</td><td class='{pnlc}'>{upnl} USDT</td><td class='{pnlc}'>{pnl if pnl is not None else '-'}</td><td>{t.get('status','-')}</td><td>{t.get('close_reason') or t.get('opened_at','-')}</td></tr>"
    return s
@app.get("/",response_class=HTMLResponse)
def home(request: Request):

    key = request.query_params.get("key")
    expected = os.getenv("DASHBOARD_KEY", "agmcis2026")

    if key != expected:
        return """<html><head><meta charset='utf-8'><title>AGMCIS Login</title></head>
        <body style='background:#020617;color:#e5e7eb;font-family:Arial;padding:40px'>
        <h1>AGMCIS Protected</h1>
        <p>請在網址後面加上 ?key=你的密碼</p>
        </body></html>"""

    a=get_account();o=get_open_trades();c=get_closed_trades();w=a.get("wins",0);l=a.get("losses",0);n=a.get("trades",0);wr=round(w/n*100,2) if n else 0
    net=round(sum((t.get("pnl_usdt") or 0) for t in c),2)
    netc="pos" if net>=0 else "neg"
    return f"""<html><head><meta charset='utf-8'><title>AGMCIS Dashboard</title><link rel='stylesheet' href='/static/css/dashboard.css'></head><body>
<h1>AGMCIS Dashboard Lite</h1><p class='muted'>Top50 掃描、模擬交易、TP/SL、Telegram 都在背景服務運作。API 即時更新模式。</p><p class='muted'>最後更新：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
<div class='grid'><div class='card'>帳戶資金<br><b id='balance'>{a.get('balance')} USDT</b></div><div class='card'>總交易<br><b id='trades'>{n}</b></div><div class='card'>勝率<br><b id='win_rate'>{wr}%</b></div><div class='card'>目前持倉<br><b id='open_count'>{len(o)}</b></div><div class='card'>淨損益<br><b class='{netc}'>{net} USDT</b></div><div class='card'>槓桿模式<br><b>2x / 3x / 5x</b></div><div class='card'>總浮盈虧<br><b id='total_open_upnl'>0 USDT</b></div><div class='card'>風險等級<br><b id='risk_level'>LOW</b></div><div class='card'>系統狀態<br><b id='system_status'>-</b></div><div class='card'>最佳交易<br><b id='best_trade'>0</b></div><div class='card'>最差交易<br><b id='worst_trade'>0</b></div><div class='card'>Profit Factor<br><b id='profit_factor'>0</b></div><div class='card'>已平倉<br><b id='total_closed_trades'>0</b></div></div>
<h2>績效分析</h2><div class="grid"><div class="card">💰 已實現收益<br><b id="total_realized">0</b></div><div class="card">🔥 最大連勝<br><b id="max_win_streak">0</b></div><div class="card">❄️ 最大連敗<br><b id="max_loss_streak">0</b></div><div class="card">📊 已平倉交易<br><b id="analytics_closed_trades">0</b></div></div><h2>目前持倉</h2><table id="open_positions_table"><tr><th>幣種</th><th>方向</th><th>槓桿</th><th>倉位價值</th><th>進場</th><th>現價</th><th>出場</th><th>停損</th><th>停利</th><th>ROI</th><th>UPNL</th><th>已實現</th><th>狀態</th><th>時間/原因</th></tr>{tr(o)}</table>
<h2>系統健康監控</h2><div class="grid"><div class="card">FastAPI<br><b id="health_api">🟢 OK</b></div><div class="card">Risk Timer<br><b id="health_risk">🟢 ON</b></div><div class="card">Daily Report<br><b id="health_report">🟢 ON</b></div><div class="card">Optimizer<br><b id="health_optimizer">🟢 ON</b></div><div class="card">最後更新<br><b id="last_update">-</b></div><div class="card">Uptime<br><b id="uptime">-</b></div></div><h2>資金曲線</h2><div class="card"><canvas id="equityChart" height="120"></canvas></div><h2>排行榜</h2><div class="grid"><div class="card"><h3>🏆 Top Winners</h3><div id="top_winners">Loading...</div></div><div class="card"><h3>💀 Top Losers</h3><div id="top_losers">Loading...</div></div></div><h2>持倉總覽</h2><div class="grid"><div class="card">🟢 獲利持倉<br><b id="profit_positions">0</b></div><div class="card">🔴 虧損持倉<br><b id="loss_positions">0</b></div><div class="card">⚪ 打平持倉<br><b id="flat_positions">0</b></div><div class="card">📈 最大浮盈<br><b id="max_profit_position">-</b></div><div class="card">📉 最大浮虧<br><b id="max_loss_position">-</b></div></div><h2>目前持倉排行</h2><div class="grid"><div class="card"><h3>🔥 最佳持倉</h3><div id="best_positions">Loading...</div></div><div class="card"><h3>⚠️ 最差持倉</h3><div id="worst_positions">Loading...</div></div><div class="card"><h3>🚨 最接近停損</h3><div id="nearest_sl">Loading...</div></div><div class="card"><h3>🎯 最接近停利</h3><div id="nearest_tp">Loading...</div></div></div><h2>最近平倉</h2><table><tr><th>幣種</th><th>方向</th><th>槓桿</th><th>倉位價值</th><th>進場</th><th>現價</th><th>出場</th><th>停損</th><th>停利</th><th>ROI</th><th>UPNL</th><th>已實現</th><th>狀態</th><th>時間/原因</th></tr>{tr(c[-10:])}</table>
<p class='muted'>服務：agmcis / agmcis-opportunity / agmcis-position / agmcis-report</p><script src='https://cdn.jsdelivr.net/npm/chart.js'></script><script src='/static/js/api.js?v=76'></script><script src='/static/js/dashboard.js?v=75'></script></body></html>"""

@app.get("/api/dashboard")
def api_dashboard():
    a = get_account()
    o = get_open_trades()
    c = get_closed_trades()
    risk = get_risk_control_status()

    positions = []

    for t in o:
        symbol = t.get("symbol")
        signal = t.get("signal")

        entry = float(t.get("entry_price") or 0)
        size = float(t.get("size_usdt") or 0)
        leverage = float(t.get("leverage") or 3)
        current = get_price(symbol)

        if current and entry:
            if signal == "做多":
                raw = (float(current) - entry) / entry * 100
            else:
                raw = (entry - float(current)) / entry * 100

            roi = round(raw * leverage, 2)
            upnl = round(size * roi / 100, 2)
        else:
            roi = 0
            upnl = 0

        positions.append({
            "symbol": symbol,
            "signal": signal,
            "leverage": leverage,
            "entry": entry,
            "current": current,
            "roi": roi,
            "upnl": upnl,
            "stoploss": t.get("stoploss"),
            "takeprofit": t.get("takeprofit"),
            "distance_to_sl": round(abs((float(current)-float(t.get("stoploss")))/float(current)*100),2) if current and t.get("stoploss") else 0,
            "distance_to_tp": round(abs((float(t.get("takeprofit"))-float(current))/float(current)*100),2) if current and t.get("takeprofit") else 0,
            "opened_at": t.get("opened_at"),
            "trailing_enabled": roi >= 5,
            "trailing_gap": 2 if roi >= 20 else 3 if roi >= 10 else 4 if roi >= 5 else 0
        })

    return {
        "balance": a.get("balance"),
        "trades": a.get("trades"),
        "wins": a.get("wins"),
        "losses": a.get("losses"),
        "open_count": len(o),
        "closed_count": len(c),
        "total_open_upnl": round(sum(p["upnl"] for p in positions), 2),
        "risk_level": "HIGH" if sum(p["upnl"] for p in positions) < -100 else "MEDIUM" if sum(p["upnl"] for p in positions) < 0 else "LOW",
        "system_status": risk.get("system_status"),
        "uptime_seconds": int(time.time() - START_TIME),
        "positions": positions
    }

@app.get("/api/equity_curve")
def api_equity_curve():
    c = get_closed_trades()
    balance = 10000
    curve = [{"index": 0, "balance": balance}]

    for i, t in enumerate(c, start=1):
        balance += float(t.get("pnl_usdt") or 0)
        curve.append({
            "index": i,
            "symbol": t.get("symbol"),
            "pnl": float(t.get("pnl_usdt") or 0),
            "balance": round(balance, 2),
            "reason": t.get("close_reason")
        })

    return {
        "start_balance": 10000,
        "current_balance": round(balance, 2),
        "points": curve
    }
@app.get("/api/stats")
def api_stats():

    trades = get_closed_trades()

    if not trades:
        return {
            "best_trade": 0,
            "worst_trade": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "profit_factor": 0
        }

    pnls = [float(t.get("pnl_usdt") or 0) for t in trades]

    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]

    best_trade = max(pnls)
    worst_trade = min(pnls)

    avg_win = round(sum(wins) / len(wins), 2) if wins else 0
    avg_loss = round(sum(losses) / len(losses), 2) if losses else 0

    gross_profit = sum(wins)
    gross_loss = sum(losses)

    profit_factor = round(
        gross_profit / gross_loss, 2
    ) if gross_loss > 0 else 0

    return {
        "best_trade": round(best_trade, 2),
        "worst_trade": round(worst_trade, 2),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "total_closed_trades": len(trades)
    }
@app.get("/api/leaderboard")
def api_leaderboard():

    trades = get_closed_trades()

    winners = sorted(
    [
        t for t in trades
        if float(t.get("pnl_usdt") or 0) > 0
    ],
    key=lambda x: float(x.get("pnl_usdt") or 0),
    reverse=True
    )[:5]

    losers = sorted(
    [
        t for t in trades
        if float(t.get("pnl_usdt") or 0) < 0
    ],
    key=lambda x: float(x.get("pnl_usdt") or 0)
    )[:5]

    return {
        "winners": [
            {
                "symbol": t.get("symbol"),
                "pnl": float(t.get("pnl_usdt") or 0)
            }
            for t in winners
        ],
        "losers": [
            {
                "symbol": t.get("symbol"),
                "pnl": float(t.get("pnl_usdt") or 0)
            }
            for t in losers
        ]
    }

@app.get("/api/analytics_pro")
def api_analytics_pro():

    trades = get_closed_trades()

    pnls = [
        float(t.get("pnl_usdt") or 0)
        for t in trades
    ]

    total_realized = round(sum(pnls), 2)

    max_win_streak = 0
    max_loss_streak = 0
    current_win = 0
    current_loss = 0

    for pnl in pnls:
        if pnl > 0:
            current_win += 1
            current_loss = 0
        elif pnl < 0:
            current_loss += 1
            current_win = 0
        else:
            current_win = 0
            current_loss = 0

        max_win_streak = max(max_win_streak, current_win)
        max_loss_streak = max(max_loss_streak, current_loss)

    return {
        "total_realized": total_realized,
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "closed_trades": len(trades)
    }

    return get_portfolio()




@app.get("/api/journal")
def api_journal():
    from journal_service import get_recent
    rows = get_recent(20)

    return [
        {
            "symbol": r[0],
            "action": r[1],
            "price": float(r[2]),
            "reason": r[3],
            "created_at": str(r[4])
        }
        for r in rows
    ]

