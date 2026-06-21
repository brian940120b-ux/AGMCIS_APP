from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
        <head>
            <meta charset="utf-8">
            <title>AGMCIS Cloud</title>
        </head>
        <body style="background:#020617;color:white;font-family:Arial;padding:40px;">
            <h1>AGMCIS Cloud Running</h1>
            <p>Dashboard 輕量模式已啟動。</p>
            <p>Top50 掃描、模擬交易、TP/SL、Telegram 推播仍由背景服務執行。</p>
        </body>
    </html>
    """
