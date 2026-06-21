from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from database_service import get_account, get_open_trades, get_closed_trades
app = FastAPI()
@app.get("/", response_class=HTMLResponse)
def home():
    a=get_account(); o=get_open_trades(); c=get_closed_trades()
    return f"<html><body style='background:#020617;color:white;font-family:Arial;padding:40px'><h1>AGMCIS Dashboard Lite</h1><h2>Account</h2><p>Balance: {a.get('balance')} USDT</p><p>Trades: {a.get('trades')}</p><p>Wins: {a.get('wins')} / Losses: {a.get('losses')}</p><h2>Open Trades: {len(o)}</h2><pre>{o}</pre><h2>Closed Trades: {len(c)}</h2><pre>{c[-5:]}</pre><p>Top50 scanner, TP/SL, Telegram are running in background.</p></body></html>"
