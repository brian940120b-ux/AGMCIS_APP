from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from market_data import get_ohlcv
from strategy import analyze_symbol

from paper_trading import (
    create_paper_trade,
    close_paper_trade,
    get_paper_summary
)

from notifier import send_telegram

app = FastAPI()


def build_trade_rows(trades):
    if not trades:
        return "<tr><td colspan='7'>目前沒有紀錄</td></tr>"

    rows = ""

    for trade in trades:
        pnl = trade.get("pnl_usdt", "-")
        exit_price = trade.get("exit_price", "-")
        closed_at = trade.get("closed_at", "-")

        rows += f"""
        <tr>
            <td>{trade["symbol"]}</td>
            <td>{trade["signal"]}</td>
            <td>{trade["entry_price"]}</td>
            <td>{exit_price}</td>
            <td>{trade["size_usdt"]}</td>
            <td>{pnl}</td>
            <td>{closed_at}</td>
        </tr>
        """

    return rows


def build_signal_cards():
    symbols = [
        "BTC/USDT",
        "ETH/USDT",
        "SOL/USDT",
        "XRP/USDT",
        "DOGE/USDT"
    ]

    cards = ""

    for symbol in symbols:
        try:
            df = get_ohlcv(symbol)
            data = analyze_symbol(symbol, df)

            if data["signal"] == "做多":
                color = "#22c55e"
                bg = "#052e16"
            elif data["signal"] == "做空":
                color = "#ef4444"
                bg = "#450a0a"
            else:
                color = "#facc15"
                bg = "#422006"

            cards += f"""
            <div class="signal-card">
                <div class="top">
                    <h3>{data["symbol"]}</h3>
                    <span style="background:{bg}; color:{color};">
                        {data["signal"]}
                    </span>
                </div>

                <h2>{data["price"]}</h2>
                <p class="muted">USDT</p>

                <div class="signal-grid">
                    <p>分數<br><b>{data["score"]}%</b></p>
                    <p>RSI<br><b>{data["rsi"]}</b></p>
                    <p>ADX<br><b>{data["adx"]}</b></p>
                    <p>ATR<br><b>{data["atr"]}</b></p>
                </div>

                <div class="trade-plan">
                    <p>進場：<b>{data["entry"]}</b></p>
                    <p>停損：<b>{data["stoploss"]}</b></p>
                    <p>止盈：<b>{data["takeprofit"]}</b></p>
                </div>

                <p class="reason">
                    {data["reason"]}
                </p>
            </div>
            """

        except Exception as e:
            cards += f"""
            <div class="signal-card">
                <h3>{symbol}</h3>
                <p style="color:#ef4444;">
                    資料讀取失敗：{e}
                </p>
            </div>
            """

    return cards


@app.get("/", response_class=HTMLResponse)
def home():
    summary = get_paper_summary()

    open_rows = build_trade_rows(summary["open_trades"])
    closed_rows = build_trade_rows(summary["closed_trades"][-10:])
    signal_cards = build_signal_cards()

    return f"""
    <html>
        <head>
            <title>AGMCIS PRO V10.4</title>
            <meta http-equiv="refresh" content="60">
        </head>

        <body>
            <h1>AGMCIS PRO</h1>
            <h2>V10.4 自動訊號引擎 + Paper Trading 控制台</h2>
            <p class="muted">每 60 秒自動刷新一次；此版本只顯示自動訊號，不會自動開倉。</p>

            <div class="stats">
                <div class="box">
                    <h3>模擬資金</h3>
                    <p>{summary["balance"]} USDT</p>
                </div>

                <div class="box">
                    <h3>總交易數</h3>
                    <p>{summary["trades"]}</p>
                </div>

                <div class="box">
                    <h3>勝率</h3>
                    <p>{summary["win_rate"]}%</p>
                </div>

                <div class="box">
                    <h3>獲利 / 虧損</h3>
                    <p>{summary["wins"]} / {summary["losses"]}</p>
                </div>
            </div>

            <div class="panel">
                <h2>自動訊號引擎</h2>
                <div class="signal-container">
                    {signal_cards}
                </div>
            </div>

            <div class="panel">
                <h2>手動開倉</h2>

                <form action="/open-trade" method="post">
                    <label>幣種</label>
                    <select name="symbol">
                        <option value="BTC/USDT">BTC/USDT</option>
                        <option value="ETH/USDT">ETH/USDT</option>
                        <option value="SOL/USDT">SOL/USDT</option>
                        <option value="XRP/USDT">XRP/USDT</option>
                        <option value="DOGE/USDT">DOGE/USDT</option>
                    </select>

                    <label>方向</label>
                    <select name="signal">
                        <option value="做多">做多</option>
                        <option value="做空">做空</option>
                    </select>

                    <label>進場價</label>
                    <input name="entry_price" type="number" step="0.0001" required>

                    <label>倉位大小 USDT</label>
                    <input name="size_usdt" type="number" step="1" value="1000" required>

                    <button type="submit">模擬開倉</button>
                </form>
            </div>

            <div class="panel">
                <h2>手動平倉</h2>

                <form action="/close-trade" method="post">
                    <label>幣種</label>
                    <select name="symbol">
                        <option value="BTC/USDT">BTC/USDT</option>
                        <option value="ETH/USDT">ETH/USDT</option>
                        <option value="SOL/USDT">SOL/USDT</option>
                        <option value="XRP/USDT">XRP/USDT</option>
                        <option value="DOGE/USDT">DOGE/USDT</option>
                    </select>

                    <label>出場價</label>
                    <input name="exit_price" type="number" step="0.0001" required>

                    <button type="submit">模擬平倉</button>
                </form>
            </div>

            <div class="panel">
                <h2>目前持倉</h2>

                <table>
                    <tr>
                        <th>幣種</th>
                        <th>方向</th>
                        <th>進場價</th>
                        <th>出場價</th>
                        <th>倉位</th>
                        <th>盈虧</th>
                        <th>時間</th>
                    </tr>
                    {open_rows}
                </table>
            </div>

            <div class="panel">
                <h2>最近平倉紀錄</h2>

                <table>
                    <tr>
                        <th>幣種</th>
                        <th>方向</th>
                        <th>進場價</th>
                        <th>出場價</th>
                        <th>倉位</th>
                        <th>盈虧</th>
                        <th>平倉時間</th>
                    </tr>
                    {closed_rows}
                </table>
            </div>
        </body>

        <style>
            body {{
                margin:0;
                background:#020617;
                color:white;
                font-family:Arial, sans-serif;
                padding:35px;
            }}

            h1 {{
                font-size:42px;
                margin-bottom:5px;
            }}

            h2 {{
                color:#cbd5e1;
            }}

            .muted {{
                color:#94a3b8;
            }}

            .stats {{
                display:flex;
                flex-wrap:wrap;
                gap:18px;
                margin:25px 0;
            }}

            .box {{
                background:#1e293b;
                border:1px solid #334155;
                border-radius:18px;
                padding:20px;
                width:210px;
            }}

            .box h3 {{
                color:#94a3b8;
                margin-top:0;
            }}

            .box p {{
                font-size:28px;
                font-weight:bold;
                margin-bottom:0;
            }}

            .panel {{
                background:#0f172a;
                border:1px solid #334155;
                border-radius:18px;
                padding:24px;
                margin-top:22px;
            }}

            .signal-container {{
                display:flex;
                flex-wrap:wrap;
                gap:18px;
            }}

            .signal-card {{
                background:linear-gradient(145deg, #1e293b, #020617);
                border:1px solid #334155;
                border-radius:18px;
                padding:20px;
                width:300px;
            }}

            .top {{
                display:flex;
                justify-content:space-between;
                align-items:center;
            }}

            .top span {{
                padding:7px 12px;
                border-radius:999px;
                font-weight:bold;
            }}

            .signal-grid {{
                display:grid;
                grid-template-columns:1fr 1fr;
                gap:10px;
                margin-top:15px;
            }}

            .signal-grid p {{
                background:#020617;
                padding:10px;
                border-radius:10px;
                color:#94a3b8;
            }}

            .signal-grid b {{
                color:white;
            }}

            .trade-plan {{
                background:#020617;
                padding:12px;
                border-radius:12px;
                margin-top:14px;
            }}

            .reason {{
                color:#cbd5e1;
                line-height:1.6;
            }}

            form {{
                display:flex;
                flex-wrap:wrap;
                gap:12px;
                align-items:end;
            }}

            label {{
                color:#94a3b8;
                display:block;
                width:100%;
            }}

            input, select {{
                background:#020617;
                color:white;
                border:1px solid #475569;
                border-radius:10px;
                padding:12px;
                min-width:170px;
            }}

            button {{
                background:#2563eb;
                color:white;
                border:none;
                border-radius:10px;
                padding:13px 20px;
                cursor:pointer;
                font-weight:bold;
            }}

            button:hover {{
                background:#1d4ed8;
            }}

            table {{
                width:100%;
                border-collapse:collapse;
                margin-top:15px;
            }}

            th, td {{
                border-bottom:1px solid #334155;
                padding:12px;
                text-align:left;
            }}

            th {{
                color:#94a3b8;
            }}
        </style>
    </html>
    """


@app.post("/open-trade")
def open_trade(
    symbol: str = Form(...),
    signal: str = Form(...),
    entry_price: float = Form(...),
    size_usdt: float = Form(...)
):
    result = create_paper_trade(
        symbol=symbol,
        entry_price=entry_price,
        signal=signal,
        size_usdt=size_usdt
    )

    if result["success"]:
        trade = result["trade"]

        send_telegram(
            f"""
📘 AGMCIS 模擬開倉

幣種：{trade["symbol"]}
方向：{trade["signal"]}
進場價：{trade["entry_price"]}
倉位：{trade["size_usdt"]} USDT
"""
        )

    return RedirectResponse("/", status_code=303)


@app.post("/close-trade")
def close_trade(
    symbol: str = Form(...),
    exit_price: float = Form(...)
):
    result = close_paper_trade(
        symbol=symbol,
        exit_price=exit_price
    )

    if result["success"]:
        trade = result["trade"]

        send_telegram(
            f"""
📕 AGMCIS 模擬平倉

幣種：{trade["symbol"]}
方向：{trade["signal"]}
進場價：{trade["entry_price"]}
出場價：{trade["exit_price"]}
盈虧：{trade["pnl_usdt"]} USDT
報酬率：{trade["pnl_pct"]}%
"""
        )

    return RedirectResponse("/", status_code=303)