from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from market_data import get_ohlcv
from market_center import get_market_center
from strategy import analyze_symbol

from paper_trading import (
    create_paper_trade,
    close_paper_trade,
    get_paper_summary
)

from analytics import get_trade_analytics
from notifier import send_telegram
from news_center import get_crypto_news
from smart_ranking import get_smart_ranking
from portfolio_manager import get_portfolio_summary

app = FastAPI()


def build_market_rows(items):
    rows = ""

    for item in items:
        color = "#22c55e" if item["change_24h"] >= 0 else "#ef4444"

        rows += f"""
        <tr>
            <td>{item["symbol"]}</td>
            <td>{item["price"]}</td>
            <td style="color:{color}; font-weight:bold;">{item["change_24h"]}%</td>
            <td>{item["volume_24h"]}</td>
        </tr>
        """

    return rows


def build_trade_rows(trades):
    if not trades:
        return "<tr><td colspan='7'>目前沒有紀錄</td></tr>"

    rows = ""

    for trade in trades:
        pnl = trade.get("pnl_usdt", "-")
        exit_price = trade.get("exit_price", "-")
        closed_at = trade.get("closed_at", "-")

        pnl_color = "#22c55e" if isinstance(pnl, (int, float)) and pnl >= 0 else "#ef4444"

        rows += f"""
        <tr>
            <td>{trade["symbol"]}</td>
            <td>{trade["signal"]}</td>
            <td>{trade["entry_price"]}</td>
            <td>{exit_price}</td>
            <td>{trade["size_usdt"]}</td>
            <td style="color:{pnl_color}; font-weight:bold;">{pnl}</td>
            <td>{closed_at}</td>
        </tr>
        """

    return rows


def build_symbol_stats_rows(symbol_stats):
    if not symbol_stats:
        return "<tr><td colspan='6'>目前沒有績效資料</td></tr>"

    rows = ""

    for item in symbol_stats:
        color = "#22c55e" if item["pnl"] >= 0 else "#ef4444"

        rows += f"""
        <tr>
            <td>{item["symbol"]}</td>
            <td>{item["trades"]}</td>
            <td>{item["wins"]}</td>
            <td>{item["losses"]}</td>
            <td>{item["win_rate"]}%</td>
            <td style="color:{color}; font-weight:bold;">{item["pnl"]} USDT</td>
        </tr>
        """

    return rows


def build_equity_svg(equity_curve):
    if not equity_curve or len(equity_curve) < 2:
        return "<p class='muted'>目前交易數不足，尚未形成資金曲線。</p>"

    width = 900
    height = 260
    padding = 35

    min_value = min(equity_curve)
    max_value = max(equity_curve)

    if max_value == min_value:
        max_value += 1

    points = []

    for index, value in enumerate(equity_curve):
        x = padding + index * ((width - padding * 2) / (len(equity_curve) - 1))
        y = height - padding - ((value - min_value) / (max_value - min_value)) * (height - padding * 2)
        points.append(f"{x},{y}")

    polyline = " ".join(points)
    line_color = "#22c55e" if equity_curve[-1] >= equity_curve[0] else "#ef4444"

    return f"""
    <svg width="100%" viewBox="0 0 {width} {height}" class="chart">
        <rect x="0" y="0" width="{width}" height="{height}" rx="18" fill="#020617" />
        <line x1="{padding}" y1="{padding}" x2="{padding}" y2="{height-padding}" stroke="#334155" />
        <line x1="{padding}" y1="{height-padding}" x2="{width-padding}" y2="{height-padding}" stroke="#334155" />

        <polyline
            points="{polyline}"
            fill="none"
            stroke="{line_color}"
            stroke-width="4"
            stroke-linecap="round"
            stroke-linejoin="round"
        />

        <text x="{padding}" y="25" fill="#94a3b8" font-size="14">High: {max_value}</text>
        <text x="{padding}" y="{height-10}" fill="#94a3b8" font-size="14">Low: {min_value}</text>
        <text x="{width-190}" y="25" fill="#cbd5e1" font-size="14">Current: {equity_curve[-1]}</text>
    </svg>
    """


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
                    <div>
                        <h3>{data["symbol"]}</h3>
                        <p class="muted">Auto Signal</p>
                    </div>
                    <span style="background:{bg}; color:{color};">{data["signal"]}</span>
                </div>

                <h2>{data["price"]}</h2>
                <p class="muted">USDT</p>

                <div class="signal-grid">
                    <p>Score<br><b>{data["score"]}%</b></p>
                    <p>RSI<br><b>{data["rsi"]}</b></p>
                    <p>ADX<br><b>{data["adx"]}</b></p>
                    <p>ATR<br><b>{data["atr"]}</b></p>
                </div>

                <div class="trade-plan">
                    <p>Entry：<b>{data["entry"]}</b></p>
                    <p>Stop Loss：<b>{data["stoploss"]}</b></p>
                    <p>Take Profit：<b>{data["takeprofit"]}</b></p>
                </div>

                <p class="reason">{data["reason"]}</p>
            </div>
            """

        except Exception as e:
            cards += f"""
            <div class="signal-card">
                <h3>{symbol}</h3>
                <p style="color:#ef4444;">資料讀取失敗：{e}</p>
            </div>
            """

    return cards

@app.get("/", response_class=HTMLResponse)
def home():
    summary = get_paper_summary()
    analytics = get_trade_analytics()
    market = get_market_center()
    portfolio = get_portfolio_summary()
    smart_ranking = get_smart_ranking()
    news_items = get_crypto_news()

    news_cards = ""

    for item in news_items:
        news_cards += f"""
        <div class="news-card">
            <h3>{item["title"]}</h3>
            <p>{item["source"]}</p>
            <a href="{item["url"]}" target="_blank">閱讀新聞</a>
        </div>
        """

    top_pick = smart_ranking[0] if smart_ranking else {
        "symbol": "-",
        "score": 0,
        "signal": "-"
    }

    open_rows = build_trade_rows(summary["open_trades"])
    closed_rows = build_trade_rows(summary["closed_trades"][-10:])
    signal_cards = build_signal_cards()
    symbol_stats_rows = build_symbol_stats_rows(analytics["symbol_stats"])
    equity_svg = build_equity_svg(analytics["equity_curve"])

    market_rows = build_market_rows(market["market_data"])
    gainer_rows = build_market_rows(market["gainers"])
    volume_rows = build_market_rows(market["volume_rank"])

    total_pnl_color = "#22c55e" if analytics["total_pnl"] >= 0 else "#ef4444"
    pf_color = "#22c55e" if analytics["profit_factor"] >= 1 else "#ef4444"

    sentiment_color = "#22c55e" if market["market_sentiment"] == "偏多" else "#facc15"
    if market["market_sentiment"] == "偏空":
        sentiment_color = "#ef4444"

    return f"""
    <html>
        <head>
            <title>AGMCIS PRO V12.1</title>
            <meta http-equiv="refresh" content="60">
        </head>

        <body>
            <aside class="sidebar">
                <div class="brand">
                    <div class="logo">A</div>
                    <div>
                        <h2>AGMCIS</h2>
                        <p>PRO Alpha</p>
                    </div>
                </div>

                <nav>
                    <a href="#dashboard">Dashboard</a>
                    <a href="#market">Market Center</a>
                    <a href="#signals">Signals</a>
                    <a href="#trading">Paper Trading</a>
                    <a href="#analytics">Analytics</a>
                    <a href="#history">History</a>
                    <a href="#news">News Center</a>
                </nav>

                <div class="sidebar-footer">
                    <p>Mode</p>
                    <b>Paper Trading</b>
                </div>
            </aside>

            <main class="main">
                <section id="dashboard" class="hero">
                    <div>
                        <h1>AGMCIS PRO</h1>
                        <p>Crypto Intelligence, Market Center & Paper Trading Dashboard</p>
                    </div>

                    <div class="status-pill">
                        MARKET CENTER ONLINE
                    </div>
                </section>

                <section class="stats">
                    <div class="box">
                        <h3>市場情緒</h3>
                        <p style="color:{sentiment_color};">{market["market_sentiment"]}</p>
                    </div>

                    <div class="box">
                        <h3>市場分數</h3>
                        <p>{market["market_score"]}%</p>
                    </div>

                    <div class="box">
                        <h3>AGMCIS 熱門標的</h3>
                        <p>{market["best_symbol"]}</p>
                    </div>
                    <div class="box">
    <h3>Smart Top Pick</h3>
    <p>{top_pick["symbol"]}</p>
</div>

<div class="box">
    <h3>AI Score</h3>
    <p>{top_pick["score"]}</p>
</div>

<div class="box">
    <h3>AI Signal</h3>
    <p>{top_pick["signal"]}</p>
</div>

                    <div class="box">
                        <h3>模擬資金</h3>
                        <p>{summary["balance"]} USDT</p>
                    </div>

                    <div class="box">
                        <h3>總盈虧</h3>
                        <p style="color:{total_pnl_color};">{analytics["total_pnl"]} USDT</p>
                    </div>

                    <div class="box">
                        <h3>Profit Factor</h3>
                        <p style="color:{pf_color};">{analytics["profit_factor"]}</p>
                    </div>
                    <div class="box">
    <h3>Portfolio Risk</h3>
    <p>{portfolio["risk_level"]}</p>
</div>

<div class="box">
    <h3>Open Positions</h3>
    <p>{portfolio["open_positions"]}</p>
</div>

<div class="box">
    <h3>Total Exposure</h3>
    <p>{portfolio["total_exposure"]} USDT</p>
</div>

<div class="box">
    <h3>Exposure Ratio</h3>
    <p>{portfolio["exposure_ratio"]}%</p>
</div>
                </section>

                <section id="market" class="grid-three">
                    <div class="panel">
                        <h2>Market Watch</h2>
                        <table>
                            <tr>
                                <th>幣種</th>
                                <th>價格</th>
                                <th>24H</th>
                                <th>成交量</th>
                            </tr>
                            {market_rows}
                        </table>
                    </div>

                    <div class="panel">
                        <h2>24H 漲幅排行</h2>
                        <table>
                            <tr>
                                <th>幣種</th>
                                <th>價格</th>
                                <th>24H</th>
                                <th>成交量</th>
                            </tr>
                            {gainer_rows}
                        </table>
                    </div>

                    <div class="panel">
                        <h2>成交量排行</h2>
                        <table>
                            <tr>
                                <th>幣種</th>
                                <th>價格</th>
                                <th>24H</th>
                                <th>成交量</th>
                            </tr>
                            {volume_rows}
                        </table>
                    </div>
                </section>

                <section id="news" class="panel">
    <h2>News Center</h2>
    <p class="muted">CoinDesk / CoinTelegraph / Decrypt RSS</p>

    <div class="news-grid">
    {news_cards}
    </div> 
</section>
                <section class="stats">
                    <div class="box">
                        <h3>總報酬率</h3>
                        <p style="color:{total_pnl_color};">{analytics["total_return_pct"]}%</p>
                    </div>

                    <div class="box">
                        <h3>最大回撤</h3>
                        <p style="color:#ef4444;">-{analytics["max_drawdown"]}%</p>
                    </div>

                    <div class="box">
                        <h3>勝率</h3>
                        <p>{summary["win_rate"]}%</p>
                    </div>

                    <div class="box">
                        <h3>最佳幣種</h3>
                        <p>{analytics["best_symbol"]}</p>
                    </div>

                    <div class="box">
                        <h3>最差幣種</h3>
                        <p>{analytics["worst_symbol"]}</p>
                    </div>
                </section>

                <section class="panel">
                    <h2>Equity Curve</h2>
                    {equity_svg}
                </section>

                <section id="signals" class="panel">
                    <div class="section-title">
                        <div>
                            <h2>Auto Signal Engine</h2>
                            <p class="muted">EMA / RSI / MACD / ADX / ATR 多因子訊號評分</p>
                        </div>
                    </div>

                    <div class="signal-container">
                        {signal_cards}
                    </div>
                </section>

                <section id="analytics" class="grid-two">
                    <div class="panel">
                        <h2>Risk Analytics</h2>

                        <div class="mini-grid">
                            <div>
                                <span>平均每筆</span>
                                <b>{analytics["avg_pnl"]} USDT</b>
                            </div>

                            <div>
                                <span>平均獲利</span>
                                <b class="green-text">{analytics["avg_win"]} USDT</b>
                            </div>

                            <div>
                                <span>平均虧損</span>
                                <b class="red-text">{analytics["avg_loss"]} USDT</b>
                            </div>

                            <div>
                                <span>盈虧比</span>
                                <b>{analytics["risk_reward_ratio"]}</b>
                            </div>

                            <div>
                                <span>最佳幣種</span>
                                <b>{analytics["best_symbol"]}</b>
                            </div>

                            <div>
                                <span>最差幣種</span>
                                <b>{analytics["worst_symbol"]}</b>
                            </div>
                        </div>
                    </div>

                    <div class="panel">
                        <h2>Symbol Performance</h2>

                        <table>
                            <tr>
                                <th>幣種</th>
                                <th>交易數</th>
                                <th>獲利</th>
                                <th>虧損</th>
                                <th>勝率</th>
                                <th>總盈虧</th>
                            </tr>
                            {symbol_stats_rows}
                        </table>
                    </div>
                </section>

                <section id="trading" class="grid-two">
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
                </section>

                <section id="history" class="panel">
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
                </section>

                <section class="panel">
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
                </section>
                <section class="panel">
    <h2>Portfolio Allocation</h2>

    <table>
        <tr>
            <th>幣種</th>
            <th>倉位 USDT</th>
            <th>配置比例</th>
        </tr>
        {
            "".join([
                f"<tr><td>{item['symbol']}</td><td>{item['size_usdt']}</td><td>{item['percent']}%</td></tr>"
                for item in portfolio["allocation"]
            ]) if portfolio["allocation"] else "<tr><td colspan='3'>目前沒有持倉配置</td></tr>"
        }
    </table>
</section>
            </main>
        </body>

        <style>
            * {{
                box-sizing:border-box;
            }}

            body {{
                margin:0;
                background:#020617;
                color:white;
                font-family:Arial, sans-serif;
                display:flex;
            }}

            .sidebar {{
                width:260px;
                height:100vh;
                position:fixed;
                left:0;
                top:0;
                background:#020617;
                border-right:1px solid #1e293b;
                padding:26px;
                display:flex;
                flex-direction:column;
                justify-content:space-between;
            }}

            .brand {{
                display:flex;
                gap:14px;
                align-items:center;
            }}

            .logo {{
                width:46px;
                height:46px;
                border-radius:14px;
                background:linear-gradient(135deg, #2563eb, #22c55e);
                display:flex;
                align-items:center;
                justify-content:center;
                font-weight:bold;
                font-size:24px;
            }}

            .brand h2 {{
                margin:0;
            }}

            .brand p {{
                margin:3px 0 0;
                color:#94a3b8;
            }}

            nav {{
                margin-top:40px;
                display:flex;
                flex-direction:column;
                gap:10px;
            }}

            nav a {{
                color:#cbd5e1;
                text-decoration:none;
                padding:12px 14px;
                border-radius:12px;
            }}

            nav a:hover {{
                background:#1e293b;
                color:white;
            }}

            .sidebar-footer {{
                background:#0f172a;
                border:1px solid #334155;
                border-radius:16px;
                padding:16px;
            }}

            .sidebar-footer p {{
                color:#94a3b8;
                margin:0 0 5px;
            }}

            .main {{
                margin-left:260px;
                width:calc(100% - 260px);
                padding:34px;
            }}

            .hero {{
                display:flex;
                justify-content:space-between;
                align-items:center;
                background:linear-gradient(135deg, #1e293b, #0f172a);
                border:1px solid #334155;
                border-radius:24px;
                padding:30px;
                margin-bottom:24px;
            }}

            .hero h1 {{
                font-size:44px;
                margin:0;
            }}

            .hero p {{
                color:#94a3b8;
                margin-bottom:0;
            }}

            .status-pill {{
                background:#052e16;
                color:#22c55e;
                padding:12px 18px;
                border-radius:999px;
                font-weight:bold;
            }}

            .stats {{
                display:grid;
                grid-template-columns:repeat(auto-fit, minmax(190px, 1fr));
                gap:18px;
                margin-bottom:24px;
            }}

            .box {{
                background:#0f172a;
                border:1px solid #334155;
                border-radius:20px;
                padding:20px;
            }}

            .box h3 {{
                color:#94a3b8;
                margin:0 0 12px;
                font-size:15px;
            }}

            .box p {{
                font-size:24px;
                font-weight:bold;
                margin:0;
            }}

            .panel {{
                background:#0f172a;
                border:1px solid #334155;
                border-radius:22px;
                padding:24px;
                margin-bottom:24px;
            }}

            .panel h2 {{
                margin-top:0;
                color:#e2e8f0;
            }}

            .muted {{
                color:#94a3b8;
            }}

            .chart {{
                margin-top:15px;
                border-radius:16px;
                overflow:hidden;
                border:1px solid #334155;
            }}

            .signal-container {{
                display:grid;
                grid-template-columns:repeat(auto-fit, minmax(280px, 1fr));
                gap:18px;
            }}

            .signal-card {{
                background:linear-gradient(145deg, #1e293b, #020617);
                border:1px solid #334155;
                border-radius:20px;
                padding:20px;
            }}

            .top {{
                display:flex;
                justify-content:space-between;
                align-items:center;
            }}

            .top h3 {{
                margin:0;
            }}

            .top span {{
                padding:7px 12px;
                border-radius:999px;
                font-weight:bold;
            }}

            .signal-card h2 {{
                font-size:32px;
                margin:18px 0 0;
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

            .grid-two {{
                display:grid;
                grid-template-columns:1fr 1fr;
                gap:24px;
            }}

            .grid-three {{
                display:grid;
                grid-template-columns:repeat(3, 1fr);
                gap:24px;
            }}

            .mini-grid {{
                display:grid;
                grid-template-columns:1fr 1fr;
                gap:14px;
            }}

            .mini-grid div {{
                background:#020617;
                border:1px solid #334155;
                border-radius:14px;
                padding:16px;
            }}

            .mini-grid span {{
                color:#94a3b8;
                display:block;
                margin-bottom:8px;
            }}

            .mini-grid b {{
                font-size:22px;
            }}

            .green-text {{
                color:#22c55e;
            }}

            .red-text {{
                color:#ef4444;
            }}

            form {{
                display:grid;
                grid-template-columns:1fr 1fr;
                gap:14px;
            }}

            label {{
                color:#94a3b8;
                grid-column:span 2;
            }}

            input, select {{
                background:#020617;
                color:white;
                border:1px solid #475569;
                border-radius:12px;
                padding:13px;
                width:100%;
            }}

            button {{
                grid-column:span 2;
                background:#2563eb;
                color:white;
                border:none;
                border-radius:12px;
                padding:14px 20px;
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
                font-size:14px;
            }}

            th, td {{
                border-bottom:1px solid #334155;
                padding:12px;
                text-align:left;
            }}

            th {{
                color:#94a3b8;
            }}

            @media (max-width: 900px) {{

    .sidebar {{
        display:none;
    }}

    .main {{
        margin-left:0;
        width:100%;
        padding:18px;
    }}

    .hero {{
        flex-direction:column;
        align-items:flex-start;
        gap:18px;
    }}

}}

/* News Center */

.news-grid {{
    display:grid;
    grid-template-columns:repeat(auto-fit, minmax(280px, 1fr));
    gap:18px;
}}

.news-card {{
    background:#020617;
    border:1px solid #334155;
    border-radius:18px;
    padding:18px;
}}

.news-card h3 {{
    font-size:16px;
    line-height:1.5;
    margin-top:0;
}}

.news-card p {{
    color:#94a3b8;
    margin:10px 0;
}}

.news-card a {{
    color:#38bdf8;
    text-decoration:none;
    font-weight:bold;
}}

.news-card a:hover {{
    text-decoration:underline;
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