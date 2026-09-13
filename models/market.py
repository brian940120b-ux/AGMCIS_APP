"""
AGMCIS Phase 2 — 市場資料模型
對應架構文件:第四章模組 1「即時市場資料中心」、第二章「所有數字必須可追溯」

每一筆資料都必須帶:
- source:資料來源(交易所 + endpoint)
- fetched_at:我方取得時間(UTC)
- exchange_ts:交易所時間戳
→ 兩者相減就是 data_freshness_ms
"""
from __future__ import annotations

from datetime import datetime, timezone
from pydantic import BaseModel, Field, model_validator


class Kline(BaseModel):
    """單根 K 線。frozen:歷史資料不可修改。"""
    model_config = {"frozen": True, "extra": "forbid"}

    symbol: str
    interval: str                      # 5m / 15m / 1h / 4h
    open_time: datetime                # 這根 K 線的開盤時間(UTC)
    open: float = Field(..., gt=0)
    high: float = Field(..., gt=0)
    low: float = Field(..., gt=0)
    close: float = Field(..., gt=0)
    volume: float = Field(..., ge=0)
    source: str                        # 例:bingx:/openApi/swap/v3/quote/klines
    fetched_at: datetime

    @model_validator(mode="after")
    def ohlc_sanity(self) -> "Kline":
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise ValueError(
                f"OHLC 不合理: O={self.open} H={self.high} L={self.low} C={self.close}"
            )
        if self.open_time.tzinfo is None or self.fetched_at.tzinfo is None:
            raise ValueError("時間必須帶 UTC 時區")
        return self


class Ticker(BaseModel):
    """最新報價快照。"""
    model_config = {"frozen": True, "extra": "forbid"}

    symbol: str
    last_price: float = Field(..., gt=0)
    bid: float | None = Field(None, gt=0)
    ask: float | None = Field(None, gt=0)
    price_change_pct: float | None = None      # 24h 漲跌幅(%)
    exchange_ts: datetime
    source: str
    fetched_at: datetime

    @property
    def spread(self) -> float | None:
        if self.bid and self.ask:
            return self.ask - self.bid
        return None

    @property
    def freshness_ms(self) -> int:
        return max(0, int((self.fetched_at - self.exchange_ts).total_seconds() * 1000))


class FundingRate(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}

    symbol: str
    rate: float                        # 例:0.0001 = 0.01%
    next_funding_time: datetime | None = None
    source: str
    fetched_at: datetime


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
