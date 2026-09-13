"""
AGMCIS Phase 1 — 訊號契約 (Signal Contract)
對應架構文件:第七章「標準訊號輸出格式」、第二章「所有數字必須可追溯」

原則:
- 所有 Agent / 策略 / LLM 的輸出都必須通過此 schema 驗證
- 驗證失敗 = 拒收,絕不修補後放行
- 需要 Pydantic v2:pip install "pydantic>=2.0"
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ── 列舉:用 enum 而不是自由字串,LLM 輸出錯字直接被擋下 ──────────────

class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    WAIT = "WAIT"          # WAIT 是正式決策,不是錯誤


class MarketRegime(str, Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    BREAKOUT = "BREAKOUT"
    FAKE_BREAKOUT = "FAKE_BREAKOUT"
    EXTREME = "EXTREME"    # 極端行情:預設禁止新單
    UNKNOWN = "UNKNOWN"    # 資料不足:視同不可交易


class RiskStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


# ── 訊號本體 ────────────────────────────────────────────────────────

class Signal(BaseModel):
    """策略引擎的唯一合法輸出格式。frozen=True:產生後不可竄改。"""

    model_config = {"frozen": True, "extra": "forbid"}

    signal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str = Field(..., description="整條決策鏈共用的追蹤 ID")
    symbol: str = Field(..., pattern=r"^[A-Z0-9]+-[A-Z0-9]+$", examples=["BTC-USDT"])
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    market_regime: MarketRegime
    direction: Direction
    confidence: int = Field(..., ge=0, le=100)

    # WAIT 時以下價格欄位必須為 None;LONG/SHORT 時必須齊全(見 model_validator)
    entry_zone: Optional[tuple[float, float]] = None
    stop_loss: Optional[float] = Field(None, gt=0)
    take_profit: Optional[list[float]] = None
    risk_reward_ratio: Optional[float] = Field(None, gt=0)
    position_size: Optional[float] = Field(None, gt=0)

    strategy: str = Field(..., min_length=1, description="策略名稱與版本,如 trend_following@1.2.0")
    reasons: list[str] = Field(..., min_length=1)
    invalidation: list[str] = Field(default_factory=list)

    risk_status: RiskStatus = RiskStatus.PENDING
    data_freshness_ms: int = Field(..., ge=0, description="資料延遲毫秒,超過門檻由風控擋下")

    # ── 驗證規則 ──

    @field_validator("timestamp")
    @classmethod
    def must_be_utc_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp 必須帶時區(UTC),禁止 naive datetime")
        return v

    @field_validator("entry_zone")
    @classmethod
    def entry_zone_ordered(cls, v):
        if v is not None:
            lo, hi = v
            if not (0 < lo <= hi):
                raise ValueError("entry_zone 必須為 (下限, 上限) 且皆 > 0")
        return v

    @model_validator(mode="after")
    def trade_fields_consistency(self) -> "Signal":
        trade_fields = [self.entry_zone, self.stop_loss, self.take_profit,
                        self.risk_reward_ratio, self.position_size]
        if self.direction == Direction.WAIT:
            if any(f is not None for f in trade_fields):
                raise ValueError("WAIT 訊號不得帶任何進出場價格欄位")
        else:
            if any(f is None for f in trade_fields):
                raise ValueError(f"{self.direction} 訊號必須包含 entry/stop/tp/rr/size 全部欄位")
            # 止損方向合理性
            lo, hi = self.entry_zone
            if self.direction == Direction.LONG and self.stop_loss >= lo:
                raise ValueError("LONG 的 stop_loss 必須低於進場區下限")
            if self.direction == Direction.SHORT and self.stop_loss <= hi:
                raise ValueError("SHORT 的 stop_loss 必須高於進場區上限")
            if not self.take_profit:
                raise ValueError("take_profit 至少需要一個目標價")
        # 極端/未知市場狀態的訊號限制:
        # - UNKNOWN(資料不足)一律只允許 WAIT,無例外
        # - EXTREME 原則上只允許 WAIT;唯一例外:momentum 型策略
        #   (strategy_id 前綴 cfg-momentum),其存在意義就是交易異常波動。
        #   修憲 2026-07-15(操作者裁決):訊號層放行 momentum,
        #   RiskGateway 仍保有最高否決權,曝險/冷卻/新聞/事件規則一條不少。
        if self.market_regime == MarketRegime.UNKNOWN \
                and self.direction != Direction.WAIT:
            raise ValueError("market_regime=UNKNOWN 時只允許 WAIT")
        if self.market_regime == MarketRegime.EXTREME \
                and self.direction != Direction.WAIT \
                and not self.strategy.startswith("cfg-momentum"):
            raise ValueError(
                "market_regime=EXTREME 時只允許 WAIT(momentum 型策略除外)")
        return self


# ── 風控決定 ────────────────────────────────────────────────────────

class RiskRuleResult(BaseModel):
    """單一風控規則的檢查結果,拒絕原因可逐條追溯(驗收標準第 6 條)。"""
    model_config = {"frozen": True}

    rule: str                     # 例:daily_loss_limit
    passed: bool
    detail: str                   # 例:今日虧損 1.8% > 上限 1.5%
    value: Optional[float] = None
    limit: Optional[float] = None


class RiskDecision(BaseModel):
    """Risk Gateway 的唯一輸出。任一規則 fail → REJECTED,不可覆寫。"""
    model_config = {"frozen": True, "extra": "forbid"}

    decision_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str
    signal_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    status: RiskStatus
    checks: list[RiskRuleResult] = Field(..., min_length=1)
    approved_position_size: Optional[float] = Field(None, gt=0, description="風控可縮小部位")

    @model_validator(mode="after")
    def status_must_match_checks(self) -> "RiskDecision":
        any_failed = any(not c.passed for c in self.checks)
        if any_failed and self.status != RiskStatus.REJECTED:
            raise ValueError("存在未通過的風控規則時,status 必須為 REJECTED(最高否決權)")
        if self.status == RiskStatus.APPROVED and self.approved_position_size is None:
            raise ValueError("APPROVED 必須附上核准的部位大小")
        return self
