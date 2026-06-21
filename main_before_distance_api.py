from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from database_service import get_account,get_open_trades,get_closed_trades
from market_data import get_price
from datetime import datetime
app=FastAPI()
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
def home():
    a=get_account();o=get_open_trades();c=get_closed_trades();w=a.get("wins",0);l=a.get("losses",0);n=a.get("trades",0);wr=round(w/n*100,2) if n else 0
    net=round(sum((t.get("pnl_usdt") or 0) for t in c),2)
    netc="pos" if net>=0 else "neg"
    return f"""<html><head><meta charset='utf-8'><title>AGMCIS Dashboard</title><style>
body{{margin:0;background:#020617;color:#e5e7eb;font-family:Arial;padding:28px}}h1{{color:#38bdf8}}.muted{{color:#94a3b8}}.grid{{display:grid;grid-template-columns:repeat(6,1fr);gap:14px}}.card{{background:#0f172a;border:1px solid #1e293b;border-radius:16px;padding:18px}}.card b{{font-size:24px}}table{{width:100%;border-collapse:collapse;background:#0f172a;margin:18px 0;border-radius:14px;overflow:hidden}}th,td{{padding:11px;border-bottom:1px solid #1e293b;text-align:left}}th{{background:#111827;color:#38bdf8}}.pos{{color:#22c55e;font-weight:bold}}.neg{{color:#ef4444;font-weight:bold}}</style></head><body>
<h1>AGMCIS Dashboard Lite</h1><p class='muted'>Top50 掃描、模擬交易、TP/SL、Telegram 都在背景服務運作。API 即時更新模式。</p><p class='muted'>最後更新：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
<div class='grid'><div class='card'>帳戶資金<br><b id='balance'>{a.get('balance')} USDT</b></div><div class='card'>總交易<br><b id='trades'>{n}</b></div><div class='card'>勝率<br><b id='win_rate'>{wr}%</b></div><div class='card'>目前持倉<br><b id='open_count'>{len(o)}</b></div><div class='card'>淨損益<br><b class='{netc}'>{net} USDT</b></div><div class='card'>槓桿模式<br><b>2x / 3x / 5x</b></div><div class='card'>總浮盈虧<br><b id='total_open_upnl'>0 USDT</b></div><div class='card'>風險等級<br><b id='risk_level'>LOW</b></div><div class='card'>最佳交易<br><b id='best_trade'>0</b></div><div class='card'>最差交易<br><b id='worst_trade'>0</b></div><div class='card'>Profit Factor<br><b id='profit_factor'>0</b></div><div class='card'>已平倉<br><b id='total_closed_trades'>0</b></div></div>
<h2>目前持倉</h2><table id="open_positions_table"><tr><th>幣種</th><th>方向</th><th>槓桿</th><th>倉位價值</th><th>進場</th><th>現價</th><th>出場</th><th>停損</th><th>停利</th><th>ROI</th><th>UPNL</th><th>已實現</th><th>狀態</th><th>時間/原因</th></tr>{tr(o)}</table>
<h2>資金曲線</h2><div class="card"><div id="equity_curve">Loading...</div></div><h2>排行榜</h2><div class="grid"><div class="card"><h3>🏆 Top Winners</h3><div id="top_winners">Loading...</div></div><div class="card"><h3>💀 Top Losers</h3><div id="top_losers">Loading...</div></div></div><h2>最近平倉</h2><table><tr><th>幣種</th><th>方向</th><th>槓桿</th><th>倉位價值</th><th>進場</th><th>現價</th><th>出場</th><th>停損</th><th>停利</th><th>ROI</th><th>UPNL</th><th>已實現</th><th>狀態</th><th>時間/原因</th></tr>{tr(c[-10:])}</table>
<p class='muted'>服務：agmcis / agmcis-opportunity / agmcis-position / agmcis-report</p><script src='/static/dashboard.js'></script></body></html>"""

@app.get("/api/dashboard")
def api_dashboard():
    a = get_account()
    o = get_open_trades()
    c = get_closed_trades()

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
            "opened_at": t.get("opened_at")
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
