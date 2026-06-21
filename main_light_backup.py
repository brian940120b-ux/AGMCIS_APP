from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from datetime import datetime

from database_service import get_account, get_open_trades, get_closed_trades

app = FastAPI()


def rows(trades):
    if not trades:
        return "<tr><td colspan='8'>目前沒有資料</td></tr>"

    html = ""
    for t in trades:
        html += f"""
        <tr>
            <td>{t.get("symbol", "-")}</td>
            <td>{t.get("signal", "-")}</td>
            <td>{t.get("entry_price", "-")}</td>
            <td>{t.get("exit_price", "-")}</td>
            <td>{t.get("size_usdt", "-")}</td>
            <td>{t.get("pnl_usdt", "-")}</td>
            <td>{t.get("status", "-")}</td>
            <td>{t.get("opened_at", "-")}</td>
        </tr>
        """
    return html


@app.get("/", response_class=HTMLResponse)
def home():
    account = get_account()
    open_trades = get_open_trades()
    closed_trades = get_closed_trades()[-10:]

    balance = account.get("balance", 0)
    wins = account.get("wins", 0)
    losses = account.get("losses", 0)
    trades = account.get("trades", 0)

    win_rate = round((wins / trades * 100), 2) if trades else 0

    return f"""
    <html>
    <head>
        <meta charset="utf-8">
        <title>AGMCIS Dashboard Lite</title>
        <meta http-equiv="refresh" content="60">
        <style>
            body {{
                margin:0;
                background:#020617;
                color:#e5e7eb;
                font-family:Arial, sans-serif;
                padding:30px;
            }}
            h1 {{ color:#38bdf8; }}
            .grid {{
                display:grid;
                grid-template-columns:repeat(4,1fr);
                gap:16px;
                margin-bottom:24px;
            }}
            .card {{
                background:#0f172a;
                padding:20px;
                border-radius:16px;
                border:1px solid #1e293b;
            }}
            .card h3 {{
                margin:0;
                color:#94a3b8;
                font-size:14px;
            }}
            .card p {{
                font-size:28px;
                margin:10px 0 0;
                font-weight:bold;
            }}
            table {{
                width:100%;
                border-collapse:collapse;
                background:#0f172a;
                border-radius:16px;
                overflow:hidden;
                margin-bottom:28px;
            }}
            th, td {{
                padding:12px;
                border-bottom:1px solid #1e293b;
                text-align:left;
                font-size:14px;
            }}
            th {{
                color:#38bdf8;
                background:#111827;
            }}
            .muted {{ color:#94a3b8; }}
        </style>
    </head>

    <body>
        <h1>AGMCIS Dashboard Lite</h1>
        <p class="muted">
            Top50 掃描、模擬交易、TP/SL、Telegram 推播由背景服務執行。
            此面板只讀 PostgreSQL，因此會快速載入。
        </p>
        <p class="muted">最後更新：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>

        <div class="grid">
            <div class="card"><h3>帳戶資金</h3><p>{balance} USDT</p></div>
            <div class="card"><h3>總交易數</h3><p>{trades}</p></div>
            <div class="card"><h3>勝率</h3><p>{win_rate}%</p></div>
            <div class="card"><h3>目前持倉</h3><p>{len(open_trades)}</p></div>
        </div>

        <h2>目前持倉</h2>
        <table>
            <tr>
                <th>幣種</th><th>方向</th><th>進場價</th><th>出場價</th>
                <th>倉位</th><th>盈虧</th><th>狀態</th><th>時間</th>
            </tr>
            {rows(open_trades)}
        </table>

        <h2>最近平倉</h2>
        <table>
            <tr>
                <th>幣種</th><th>方向</th><th>進場價</th><th>出場價</th>
                <th>倉位</th><th>盈虧</th><th>狀態</th><th>時間</th>
            </tr>
            {rows(closed_trades)}
        </table>
    </body>
    </html>
    """
