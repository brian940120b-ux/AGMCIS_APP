"""
AGMCIS Phase 2 — BingX 行情客戶端(公開行情,不需要 API Key)
對應架構文件:第十章「BingX REST API」、模組 1「REST 補資料與校正」

設計重點:
- 只讀公開行情:K 線、Ticker、資金費率(下單留到 V6.9,且用獨立權限)
- 逾時 + 有限重試(指數退避),重試次數寫進日誌
- 欄位解析集中在 _parse_* 函數:BingX 改版時只改一處
- 解析失敗時把原始回應留在例外訊息裡,方便回報除錯

需要:pip install requests
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import requests

from core.logging import get_logger
from models.market import FundingRate, Kline, Ticker, utcnow

log = get_logger("bingx")

BASE_URL = "https://open-api.bingx.com"
KLINES_EP = "/openApi/swap/v3/quote/klines"
TICKER_EP = "/openApi/swap/v2/quote/ticker"
FUNDING_EP = "/openApi/swap/v2/quote/premiumIndex"

VALID_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"}


class BingXError(Exception):
    """API 呼叫或解析失敗。上游收到此例外 = 資料不可用 = WAIT。"""


class BingXClient:
    def __init__(self, timeout_s: float = 5.0, max_retries: int = 3):
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.session = requests.Session()

    # ── 底層請求:逾時 + 重試 ──────────────────────────────

    def _get(self, endpoint: str, params: dict[str, Any]) -> Any:
        url = BASE_URL + endpoint
        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout_s)
                resp.raise_for_status()
                body = resp.json()
                # BingX 慣例:{"code": 0, "msg": "", "data": ...},code != 0 為業務錯誤
                if isinstance(body, dict) and body.get("code", 0) != 0:
                    raise BingXError(f"BingX 業務錯誤 code={body.get('code')} msg={body.get('msg')}")
                return body.get("data", body) if isinstance(body, dict) else body
            except (requests.RequestException, ValueError, BingXError) as e:
                last_err = e
                log.warning(
                    f"BingX 請求失敗,第 {attempt}/{self.max_retries} 次",
                    extra={"data": {"endpoint": endpoint, "params": params, "error": str(e)}},
                )
                if attempt < self.max_retries:
                    time.sleep(0.5 * (2 ** (attempt - 1)))   # 0.5s → 1s → 2s
        raise BingXError(f"BingX 請求最終失敗 endpoint={endpoint}: {last_err}")

    # ── 對外工具函數(之後就是 LLM 的 tools)───────────────

    def get_klines(self, symbol: str, interval: str, limit: int = 100) -> list[Kline]:
        if interval not in VALID_INTERVALS:
            raise ValueError(f"不支援的週期 {interval},可用:{sorted(VALID_INTERVALS)}")
        data = self._get(KLINES_EP, {"symbol": symbol, "interval": interval, "limit": limit})
        fetched = utcnow()
        try:
            klines = [_parse_kline(row, symbol, interval, fetched) for row in data]
        except (KeyError, TypeError, ValueError) as e:
            raise BingXError(f"K 線解析失敗({e});原始資料樣本:{str(data)[:300]}")
        klines.sort(key=lambda k: k.open_time)   # 保證由舊到新
        log.info(f"取得 {symbol} {interval} K 線 {len(klines)} 根",
                 extra={"data": {"source": KLINES_EP}})
        return klines

    def get_klines_history(self, symbol: str, interval: str,
                           start: datetime, end: datetime) -> list[Kline]:
        """
        分頁抓取歷史 K 線。
        BingX 的 klines 端點會把結果錨定在最新資料,因此採「由後往前」翻頁:
        每次只帶 endTime,拿到一批就把 endTime 推到該批最早一根之前,直到回到 start。
        含停滯保護:任何一頁沒有把時間往前推,直接中止避免無限迴圈。
        """
        if interval not in VALID_INTERVALS:
            raise ValueError(f"不支援的週期 {interval}")
        start_ms = int(start.timestamp() * 1000)
        cursor_end_ms = int(end.timestamp() * 1000)
        fetched = utcnow()
        out: list[Kline] = []

        while cursor_end_ms > start_ms:
            data = self._get(KLINES_EP, {
                "symbol": symbol, "interval": interval,
                "endTime": cursor_end_ms, "limit": 1000})
            try:
                batch = [_parse_kline(row, symbol, interval, fetched) for row in data]
            except (KeyError, TypeError, ValueError) as e:
                raise BingXError(f"歷史 K 線解析失敗({e});樣本:{str(data)[:300]}")
            if not batch:
                break
            batch.sort(key=lambda k: k.open_time)
            out = batch + out                      # 由後往前:整批放到前面
            earliest_ms = int(batch[0].open_time.timestamp() * 1000)
            log.info(f"歷史下載 {symbol} {interval} 累計 {len(out)} 根 "
                     f"(最早至 {batch[0].open_time.date()})")
            next_end = earliest_ms - 1
            if next_end >= cursor_end_ms:
                break                              # 停滯保護
            cursor_end_ms = next_end

        # 過濾範圍 + 去重 + 排序(交易所偶爾回重複頁)
        seen: set = set()
        unique: list[Kline] = []
        for k in sorted(out, key=lambda k: k.open_time):
            ms = int(k.open_time.timestamp() * 1000)
            if start_ms <= ms <= int(end.timestamp() * 1000) and k.open_time not in seen:
                seen.add(k.open_time)
                unique.append(k)
        return unique

    def get_all_tickers(self) -> list[dict]:
        """全市場 24h 行情(不帶 symbol)。用於幣種篩選,回傳原始 dict 清單。"""
        data = self._get(TICKER_EP, {})
        if isinstance(data, dict):
            data = [data]
        return [d for d in (data or []) if isinstance(d, dict)]

    def get_ticker(self, symbol: str) -> Ticker:
        data = self._get(TICKER_EP, {"symbol": symbol})
        fetched = utcnow()
        try:
            return _parse_ticker(data, symbol, fetched)
        except (KeyError, TypeError, ValueError) as e:
            raise BingXError(f"Ticker 解析失敗({e});原始資料樣本:{str(data)[:300]}")

    def get_funding_rate(self, symbol: str) -> FundingRate:
        data = self._get(FUNDING_EP, {"symbol": symbol})
        fetched = utcnow()
        try:
            return _parse_funding(data, symbol, fetched)
        except (KeyError, TypeError, ValueError) as e:
            raise BingXError(f"資金費率解析失敗({e});原始資料樣本:{str(data)[:300]}")


# ── 欄位解析:BingX 改版只改這裡 ──────────────────────────

def _ms_to_dt(ms: int | str) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)


def _parse_kline(row: Any, symbol: str, interval: str, fetched: datetime) -> Kline:
    # BingX v3 klines 常見兩種格式:dict 或 list
    if isinstance(row, dict):
        return Kline(
            symbol=symbol, interval=interval,
            open_time=_ms_to_dt(row["time"]),
            open=float(row["open"]), high=float(row["high"]),
            low=float(row["low"]), close=float(row["close"]),
            volume=float(row["volume"]),
            source=f"bingx:{KLINES_EP}", fetched_at=fetched,
        )
    # list 格式:[time, open, high, low, close, volume, ...]
    return Kline(
        symbol=symbol, interval=interval,
        open_time=_ms_to_dt(row[0]),
        open=float(row[1]), high=float(row[2]),
        low=float(row[3]), close=float(row[4]), volume=float(row[5]),
        source=f"bingx:{KLINES_EP}", fetched_at=fetched,
    )


def _f(v) -> float | None:
    """寬鬆數字轉換:BingX 有時回字串、有時帶 % 或缺欄位。"""
    if v is None:
        return None
    try:
        return float(str(v).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _parse_ticker(data: Any, symbol: str, fetched: datetime) -> Ticker:
    d = data[0] if isinstance(data, list) else data
    ts_raw = d.get("time") or d.get("closeTime") or d.get("E")
    return Ticker(
        symbol=symbol,
        last_price=float(d["lastPrice"]),
        bid=float(d["bidPrice"]) if d.get("bidPrice") else None,
        ask=float(d["askPrice"]) if d.get("askPrice") else None,
        exchange_ts=_ms_to_dt(ts_raw) if ts_raw else fetched,
        price_change_pct=_f(d.get("priceChangePercent")),
        source=f"bingx:{TICKER_EP}", fetched_at=fetched,
    )


def _parse_funding(data: Any, symbol: str, fetched: datetime) -> FundingRate:
    d = data[0] if isinstance(data, list) else data
    nft = d.get("nextFundingTime")
    return FundingRate(
        symbol=symbol,
        rate=float(d["lastFundingRate"]),
        next_funding_time=_ms_to_dt(nft) if nft else None,
        source=f"bingx:{FUNDING_EP}", fetched_at=fetched,
    )
