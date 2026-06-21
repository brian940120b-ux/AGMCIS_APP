from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from database_service import get_account,get_open_trades,get_closed_trades
from market_data import get_price
from datetime import datetime
app=FastAPI()
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
    return f"""<html><head><meta charset='utf-8'><meta http-equiv='refresh' content='60'><title>AGMCIS Dashboard</title><style>
body{{margin:0;background:#020617;color:#e5e7eb;font-family:Arial;padding:28px}}h1{{color:#38bdf8}}.muted{{color:#94a3b8}}.grid{{display:grid;grid-template-columns:repeat(6,1fr);gap:14px}}.card{{background:#0f172a;border:1px solid #1e293b;border-radius:16px;padding:18px}}.card b{{font-size:24px}}table{{width:100%;border-collapse:collapse;background:#0f172a;margin:18px 0;border-radius:14px;overflow:hidden}}th,td{{padding:11px;border-bottom:1px solid #1e293b;text-align:left}}th{{background:#111827;color:#38bdf8}}.pos{{color:#22c55e;font-weight:bold}}.neg{{color:#ef4444;font-weight:bold}}</style></head><body>
<h1>AGMCIS Dashboard Lite</h1><p class='muted'>Top50 掃描、模擬交易、TP/SL、Telegram 都在背景服務運作。每 60 秒自動刷新。</p><p class='muted'>最後更新：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
<div class='grid'><div class='card'>帳戶資金<br><b>{a.get('balance')} USDT</b></div><div class='card'>總交易<br><b>{n}</b></div><div class='card'>勝率<br><b>{wr}%</b></div><div class='card'>目前持倉<br><b>{len(o)}</b></div><div class='card'>淨損益<br><b class='{netc}'>{net} USDT</b></div><div class='card'>槓桿模式<br><b>2x / 3x / 5x</b></div></div>
<h2>目前持倉</h2><table><tr><th>幣種</th><th>方向</th><th>槓桿</th><th>倉位價值</th><th>進場</th><th>現價</th><th>出場</th><th>停損</th><th>停利</th><th>ROI</th><th>UPNL</th><th>已實現</th><th>狀態</th><th>時間/原因</th></tr>{tr(o)}</table>
<h2>最近平倉</h2><table><tr><th>幣種</th><th>方向</th><th>槓桿</th><th>倉位價值</th><th>進場</th><th>現價</th><th>出場</th><th>停損</th><th>停利</th><th>ROI</th><th>UPNL</th><th>已實現</th><th>狀態</th><th>時間/原因</th></tr>{tr(c[-10:])}</table>
<p class='muted'>服務：agmcis / agmcis-opportunity / agmcis-position / agmcis-report</p></body></html>"""
