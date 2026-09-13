"""
新系統 — 交易所即時串流 · 2026-09-08

═══ 為什麼從輪詢改成推播 ═══
輪詢版每 10 秒抓一次全市場行情。那是「近乎即時」,不是即時 ——
價格在那 10 秒裡怎麼走,面板看不到,而交易員看盤時看的正是那個過程。

BingX 有公開的 WebSocket 行情串流,**逐筆推送**(實測每秒數筆)。
改成:交易所推 → 本進程記憶體 → SSE 推到瀏覽器,全程沒有輪詢。

═══ 這一層的紀律,跟輪詢版完全一樣 ═══
**只讀不寫。不進任何決策。**
記帳仍是每日一次(訊號用收盤、成交在隔日開盤)—— 那是策略的節奏。
即時價格只給人看。讓它流進決策,策略就從日線變成盯盤,
而回測的 Calmar 1.33 是在日線規則下算出來的。

推播比輪詢更誘人(數字一直在跳),所以這條紀律更要寫死在測試裡。

═══ 斷線處理 ═══
· 自動重連,指數退避(1s → 30s 上限)
· 斷線期間**明確標記為過期**,絕不拿最後一次的價格假裝是即時的 ——
  一條靜止不動卻標著「即時」的價格,比沒有價格危險
· 交易所送 "Ping" 要回 "Pong",不回會被踢
· 訊息是 gzip 壓縮的二進位
"""
from __future__ import annotations

import asyncio
import gzip
import io
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.logging import get_logger

log = get_logger("portfolio.stream")

WS_URL = "wss://open-api-swap.bingx.com/swap-market"
STALE_S = 20.0            # 超過這麼久沒收到該幣的更新 = 過期
RECONNECT_MAX_S = 30.0

_LOCK = threading.Lock()
_PX: dict[str, tuple[float, float]] = {}     # symbol -> (price, 收到的時間)
_STATE = {"connected": False, "since": 0.0, "msgs": 0, "error": None,
          "started": False}


def _decode(raw) -> str:
    if isinstance(raw, bytes):
        try:
            return gzip.GzipFile(fileobj=io.BytesIO(raw)).read().decode()
        except Exception:
            return raw.decode(errors="replace")
    return raw


async def _run(symbols: list[str]) -> None:
    import websockets
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=20,
                                          close_timeout=5) as ws:
                for i, sym in enumerate(symbols):
                    await ws.send(json.dumps({
                        "id": str(i), "reqType": "sub",
                        "dataType": f"{sym}@lastPrice"}))
                with _LOCK:
                    _STATE.update({"connected": True, "since": time.time(),
                                   "error": None})
                backoff = 1.0
                log.info(f"行情串流已連線,訂閱 {len(symbols)} 個幣")
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=60)
                    txt = _decode(raw)
                    if txt == "Ping":
                        await ws.send("Pong")
                        continue
                    try:
                        msg = json.loads(txt)
                    except json.JSONDecodeError:
                        continue
                    d = msg.get("data")
                    if not isinstance(d, dict):
                        continue
                    sym, px = d.get("s"), d.get("c")
                    if not sym or px is None:
                        continue
                    try:
                        val = float(px)
                    except (TypeError, ValueError):
                        continue
                    with _LOCK:
                        _PX[sym] = (val, time.time())
                        _STATE["msgs"] += 1
        except Exception as e:
            with _LOCK:
                _STATE.update({"connected": False,
                               "error": f"{type(e).__name__}: {e}"})
            log.warning(f"行情串流中斷,{backoff:.0f}s 後重連:{e}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX_S)


def start(symbols: list[str] | None = None) -> None:
    """在背景執行緒啟動串流。重複呼叫只會啟動一次。"""
    with _LOCK:
        if _STATE["started"]:
            return
        _STATE["started"] = True
    if symbols is None:
        try:
            from portfolio.paper import SYMBOLS
            symbols = list(SYMBOLS)
        except Exception:
            symbols = []
    if not symbols:
        log.warning("無訂閱標的,串流不啟動")
        return

    def _thread():
        try:
            asyncio.run(_run(symbols))
        except Exception as e:
            log.warning(f"串流執行緒結束:{e}")
            with _LOCK:
                _STATE.update({"connected": False, "started": False,
                               "error": f"{type(e).__name__}: {e}"})

    threading.Thread(target=_thread, daemon=True, name="px-stream").start()


def prices(symbols: list[str] | None = None) -> dict[str, float]:
    """目前價格。**只回沒過期的** —— 斷線後不拿最後一次的價格充數。"""
    now = time.time()
    with _LOCK:
        out = {s: p for s, (p, t) in _PX.items() if now - t <= STALE_S}
    return {s: out[s] for s in symbols if s in out} if symbols else out


def state() -> dict:
    with _LOCK:
        s = dict(_STATE)
        s["symbols"] = len(_PX)
        s["uptime_s"] = (time.time() - s["since"]) if s["since"] else 0.0
    return s
