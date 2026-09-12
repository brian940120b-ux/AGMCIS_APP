"""
BingX WebSocket 行情(Master Prompt 第四十九節)。

第四十九節說得很清楚:有 WebSocket 就優先用,REST 當 fallback 與對帳。
在這個模組出現以前,所有東西都走 REST 輪詢 —— 每個標的每輪一次,
而且兩次輪詢之間發生的事完全看不到。

## 用 ccxt.pro,不自己實作協定

第五節:「不要靠模型記憶猜 API」。BingX 的 WebSocket 有自己的訂閱格式、
心跳、壓縮方式與重連語意,手寫等於用記憶猜。ccxt.pro 是隨 ccxt 一起
安裝的,而且與 REST 那一側用同一組 symbol 格式。

## 三條規則

**一、過期的價格等於沒有價格。**
WebSocket 斷線的時候最危險的行為不是報錯,是**繼續回傳最後一次收到的
價格**。那個價格看起來完全正常,而系統會用它算停損、算強平、算損益。
所以每一筆都有時間戳,超過 `max_age_seconds` 就回 None,由呼叫端
退回 REST。

**二、WebSocket 不是訂單與部位的真相來源。**
它可以更快地知道「有事情發生了」,但「現在到底有什麼部位」永遠以
REST 對帳為準(第十七節)。一個把 WS 事件當成部位真相的系統,
在漏掉一個訊息之後會一直錯下去,而且不會發現。

**三、重連要有上限。**
第四十八節禁止無限重試。連不上就讓它停,並且讓 `/health` 看得到 ——
一個安靜地永遠重連的背景執行緒,看起來跟正常運作一模一樣。

## 為什麼跑在自己的執行緒

系統其他部分是同步的。把整個 codebase 改成 async 是一個大得多的改動,
而且沒有必要 —— 這一層要的只是「背景持續更新一個 dict」。
執行緒 + 自己的事件迴圈就夠了,而且它掛掉不會拖垮主流程。
"""
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("agmcis.exchange.bingx.stream")

# 超過這麼久沒更新就當成沒有價格。
# 15 秒:BingX 的 ticker 推送頻率遠高於此,超過代表連線有問題。
DEFAULT_MAX_AGE_SECONDS = 15.0

# 重連的退避與上限。第四十八節:禁止無限重試。
RECONNECT_BACKOFF_SECONDS = 2.0
MAX_RECONNECTS = 20


@dataclass
class Quote:
    symbol: str
    price: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    received_at: float = 0.0

    def is_fresh(self, max_age_seconds=DEFAULT_MAX_AGE_SECONDS, now=None):
        return ((now or time.time()) - self.received_at) <= max_age_seconds

    def to_dict(self):
        return {
            "symbol": self.symbol, "price": self.price,
            "bid": self.bid, "ask": self.ask,
            "received_at": self.received_at,
        }


@dataclass
class StreamStatus:
    running: bool = False
    connected: bool = False
    symbols: List[str] = field(default_factory=list)
    updates: int = 0
    reconnects: int = 0
    last_update_at: Optional[float] = None
    last_error: Optional[str] = None
    gave_up: bool = False

    def to_dict(self):
        return dict(self.__dict__)


class MarketStream:
    """
    背景維護一份即時報價。

    `get_price()` 只在報價夠新的時候回傳它 —— 否則回 None,
    呼叫端退回 REST。這個模組**不會**自己去打 REST:
    那會讓「WebSocket 有沒有在運作」變得看不出來。
    """

    def __init__(self, symbols, exchange_factory=None,
                 max_age_seconds=DEFAULT_MAX_AGE_SECONDS,
                 max_reconnects=MAX_RECONNECTS,
                 backoff_seconds=RECONNECT_BACKOFF_SECONDS,
                 sleep=None):
        self.symbols = list(symbols or [])
        self.max_age_seconds = float(max_age_seconds)
        self.max_reconnects = int(max_reconnects)
        self.backoff_seconds = float(backoff_seconds)

        self._factory = exchange_factory or _default_exchange
        self._sleep = sleep or time.sleep
        self._quotes: Dict[str, Quote] = {}
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self.status = StreamStatus(symbols=list(self.symbols))

    # ---------------- 對外 ----------------

    def get_price(self, symbol):
        """
        最新價。**過期就回 None。**

        斷線時繼續回傳最後一次收到的價格,是這一層最危險的失敗模式:
        那個價格看起來完全正常,而系統會用它算停損、算強平、算損益。
        """
        with self._lock:
            quote = self._quotes.get(symbol)

        if quote is None or not quote.is_fresh(self.max_age_seconds):
            return None
        return quote.price

    def get_quote(self, symbol):
        with self._lock:
            quote = self._quotes.get(symbol)

        if quote is None or not quote.is_fresh(self.max_age_seconds):
            return None
        return quote

    def snapshot(self):
        """所有報價,**含過期的** —— 診斷時需要看到「它多舊」。"""
        now = time.time()
        with self._lock:
            return {
                symbol: {
                    **quote.to_dict(),
                    "age_seconds": round(now - quote.received_at, 2),
                    "fresh": quote.is_fresh(self.max_age_seconds, now=now),
                }
                for symbol, quote in self._quotes.items()
            }

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return self

        self._stop.clear()
        self.status = StreamStatus(running=True, symbols=list(self.symbols))
        self._thread = threading.Thread(
            target=self._run, name="agmcis-bingx-stream", daemon=True,
        )
        self._thread.start()
        return self

    def stop(self, timeout=5.0):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self.status.running = False
        self.status.connected = False
        return self

    # ---------------- 內部 ----------------

    def _record(self, symbol, ticker):
        price = ticker.get("last") or ticker.get("close")
        if price is None:
            return

        with self._lock:
            self._quotes[symbol] = Quote(
                symbol=symbol, price=float(price),
                bid=ticker.get("bid"), ask=ticker.get("ask"),
                received_at=time.time(),
            )

        self.status.updates += 1
        self.status.last_update_at = time.time()
        self.status.connected = True

    def _run(self):
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(self._loop())
        except Exception as exc:
            logger.exception("WebSocket 執行緒異常結束")
            self.status.last_error = f"{type(exc).__name__}: {exc}"
        finally:
            self.status.running = False
            self.status.connected = False
            try:
                loop.close()
            except Exception:
                pass

    async def _loop(self):
        import asyncio

        exchange = self._factory()

        try:
            while not self._stop.is_set():
                try:
                    tickers = await exchange.watch_tickers(self.symbols)
                    for symbol, ticker in (tickers or {}).items():
                        self._record(symbol, ticker)
                except Exception as exc:
                    self.status.connected = False
                    self.status.last_error = f"{type(exc).__name__}: {exc}"
                    self.status.reconnects += 1

                    if self.status.reconnects > self.max_reconnects:
                        # 第四十八節:禁止無限重試。停下來,並且讓
                        # /health 看得到 —— 一個安靜地永遠重連的執行緒,
                        # 看起來跟正常運作一模一樣。
                        self.status.gave_up = True
                        logger.critical(
                            "WebSocket 重連 %s 次後放棄 | %s | "
                            "行情改走 REST,延遲會變高",
                            self.status.reconnects, exc,
                        )
                        return

                    logger.warning(
                        "WebSocket 斷線,第 %s 次重連 | %s",
                        self.status.reconnects, exc,
                    )
                    await asyncio.sleep(
                        self.backoff_seconds * min(self.status.reconnects, 8)
                    )
        finally:
            try:
                await exchange.close()
            except Exception:
                pass


def _default_exchange():
    """
    ccxt.pro 的 BingX。**不帶金鑰** —— 這一層只看公開行情。

    帳戶與訂單的 WebSocket 需要簽名,而那條路徑在 LiveBroker 存在
    之前不該打開:一個能收到帳戶事件的連線,離一個能送出訂單的連線
    只差幾行程式碼。
    """
    import ccxt.pro as ccxtpro

    return ccxtpro.bingx({
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    })


_stream = None


def get_stream():
    """全域串流。沒啟動過就回 None —— **不自動啟動**。"""
    return _stream


def set_stream(stream):
    global _stream
    _stream = stream


def start_stream(symbols=None, **kwargs):
    """
    啟動全域串流。

    重複呼叫是安全的:已經在跑就直接回傳原本那一個,
    不會產生第二條連線(第二條連線會讓交易所的連線數上限提早用完)。
    """
    global _stream

    if _stream is not None and _stream.status.running:
        return _stream

    from agmcis.config import settings

    if not getattr(settings, "WEBSOCKET_ENABLED", False):
        logger.info("WebSocket 未啟用(WEBSOCKET_ENABLED=false),行情走 REST")
        return None

    _stream = MarketStream(
        symbols or list(settings.WATCHLIST_SYMBOLS), **kwargs
    ).start()

    logger.info("WebSocket 行情已啟動 | %s 檔", len(_stream.symbols))
    return _stream


def stop_stream():
    global _stream
    if _stream is not None:
        _stream.stop()
        _stream = None
