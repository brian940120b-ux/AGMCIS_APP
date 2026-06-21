from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
def home():
    return "<html><body style='background:#020617;color:white;font-family:Arial;padding:40px'><h1>AGMCIS Cloud Running</h1><p>Dashboard Lite OK.</p><p>Top50 scanner, paper trading, TP/SL and Telegram are running in background services.</p></body></html>"
