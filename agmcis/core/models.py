"""
系統的資料結構。

設計重點:TradeIntent 是 AI Agent 與策略層**唯一**能產出的東西,
而它在建構時就強制驗證停損 —— 一個沒有停損、或停損放在錯誤方向的 TradeIntent
根本無法被建立出來。這讓「開倉必須有停損」變成型別層級的保證,
而不是靠每個呼叫端自己記得檢查。

這一層不得 import 任何 exchange、DB 或 web 模組。
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

from agmcis.core.enums import (
    ConfidenceBand,
    Direction,
    MarketType,
    OrderSide,
    OrderState,
    OrderType,
    PositionSide,
)
from agmcis.core.errors import TradingRuleViolation


def _utcnow():
    return datetime.now(timezone.utc)


def _as_direction(value):
    direction = Direction.parse(value)
    if direction is None:
        raise ValueError(f"無法辨識的交易方向: {value!r}")
    return direction


# ---------------- 訊號 ----------------

@dataclass
class Signal:
    """
    策略 / 分析層產出的觀察結果。Signal 本身不是下單指令 ——
    它可以是 WAIT,而 WAIT 是完全合法的結論。
    """
    symbol: str
    market_type: MarketType
    direction: Direction
    timeframe: str
    strategy: str
    confidence: Optional[float] = None
    score: Optional[float] = None
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    market_regime: Optional[str] = None
    reasons: List[str] = field(default_factory=list)
    data_ok: bool = True
    data_error: Optional[str] = None
    created_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self):
        self.direction = _as_direction(self.direction)
        self.market_type = MarketType.parse(self.market_type, MarketType.PERPETUAL)

    @property
    def confidence_band(self):
        return ConfidenceBand.of(self.confidence)

    @property
    def is_tradable(self):
        """
        可以拿去建立 TradeIntent 的條件。
        資料異常一律不可交易 —— 資料壞掉不等於市場中性。
        """
        return (
            self.data_ok
            and self.direction.is_directional
            and self.entry is not None
            and self.stop_loss is not None
        )

    @property
    def risk_reward(self):
        if None in (self.entry, self.stop_loss, self.take_profit):
            return None
        risk = abs(float(self.entry) - float(self.stop_loss))
        if risk == 0:
            return None
        reward = abs(float(self.take_profit) - float(self.entry))
        return round(reward / risk, 2)

    def to_dict(self):
        return _serialize(self)


# ---------------- 交易意圖 ----------------

@dataclass
class TradeIntent:
    """
    AI Agent / 策略層唯一允許的輸出。

    它刻意**不包含**倉位大小與槓桿 —— 那是 Risk Engine 的職責。
    Agent 說「我想做多 BTC,停損在這裡」,由 Risk Engine 決定「可以,size 這麼大」。

    建構時就會驗證:
      - 方向必須是明確的多或空
      - 停損必須存在,且在正確方向
      - 停利若有設定,也必須在正確方向
    驗證失敗直接拋 TradingRuleViolation,不會產生一個無效的 intent。
    """
    symbol: str
    market_type: MarketType
    direction: Direction
    entry: float
    stop_loss: float
    take_profit: Optional[float] = None
    confidence: Optional[float] = None
    score: Optional[float] = None
    strategy: Optional[str] = None
    timeframe: Optional[str] = None
    market_regime: Optional[str] = None
    reasons: List[str] = field(default_factory=list)
    agent_votes: Dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self):
        self.direction = _as_direction(self.direction)
        self.market_type = MarketType.parse(self.market_type, MarketType.PERPETUAL)

        if not self.direction.is_directional:
            raise TradingRuleViolation(
                "direction", f"{self.symbol} TradeIntent 必須是明確方向,收到 {self.direction.value}"
            )

        self.entry = float(self.entry)
        if self.entry <= 0:
            raise TradingRuleViolation("entry", f"{self.symbol} 進場價必須大於 0")

        if self.stop_loss is None:
            raise TradingRuleViolation(
                "stop_loss",
                f"{self.symbol} 沒有停損。沒有停損的倉位等於沒有風險上限,一律不允許。",
            )

        self.stop_loss = float(self.stop_loss)
        if not self._on_correct_side(self.stop_loss, is_stop=True):
            raise TradingRuleViolation(
                "stop_loss",
                f"{self.symbol} {self.direction.value} 的停損 {self.stop_loss} 方向錯誤"
                f"(進場 {self.entry})。放在錯邊會在下一刻立即停損出場。",
            )

        if self.take_profit is not None:
            self.take_profit = float(self.take_profit)
            if not self._on_correct_side(self.take_profit, is_stop=False):
                raise TradingRuleViolation(
                    "take_profit",
                    f"{self.symbol} {self.direction.value} 的停利 {self.take_profit} "
                    f"方向錯誤(進場 {self.entry})。",
                )

    def _on_correct_side(self, price, is_stop):
        if price <= 0:
            return False
        below = price < self.entry
        if self.direction is Direction.LONG:
            return below if is_stop else not below
        return (not below) if is_stop else below

    @property
    def stop_distance_pct(self):
        """停損距離佔進場價的百分比。Position Sizing 會用到(Phase 5)。"""
        return abs(self.entry - self.stop_loss) / self.entry * 100

    @property
    def risk_reward(self):
        if self.take_profit is None:
            return None
        risk = abs(self.entry - self.stop_loss)
        if risk == 0:
            return None
        return round(abs(self.take_profit - self.entry) / risk, 2)

    @property
    def order_side(self):
        return self.direction.order_side

    @property
    def position_side(self):
        return PositionSide.LONG if self.direction is Direction.LONG else PositionSide.SHORT

    @classmethod
    def from_signal(cls, signal: Signal, **overrides):
        """從 Signal 建立 TradeIntent。不可交易的 Signal 會被擋下。"""
        if not signal.is_tradable:
            raise TradingRuleViolation(
                "signal",
                f"{signal.symbol} 的 Signal 不可交易"
                f"(data_ok={signal.data_ok} direction={signal.direction.value} "
                f"entry={signal.entry} sl={signal.stop_loss})",
            )

        payload = {
            "symbol": signal.symbol,
            "market_type": signal.market_type,
            "direction": signal.direction,
            "entry": signal.entry,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "confidence": signal.confidence,
            "score": signal.score,
            "strategy": signal.strategy,
            "timeframe": signal.timeframe,
            "market_regime": signal.market_regime,
            "reasons": list(signal.reasons),
        }
        payload.update(overrides)
        return cls(**payload)

    def to_dict(self):
        return _serialize(self)


# ---------------- 風控裁決 ----------------

@dataclass
class RiskDecision:
    """
    Risk Engine 對一個 TradeIntent 的裁決。

    approved=False 時 size_usdt 與 leverage 沒有意義。
    Execution Engine 只接受 approved=True 的裁決。
    """
    intent: TradeIntent
    approved: bool
    size_usdt: Optional[float] = None
    leverage: Optional[float] = None
    reason: Optional[str] = None
    blockers: List[str] = field(default_factory=list)
    # 通過了但值得知道的事:例如某幾檔因為算不出相關係數而被當成相關。
    # 這些不影響 approved,但會進 log 與 Dashboard。
    warnings: List[str] = field(default_factory=list)
    decided_at: datetime = field(default_factory=_utcnow)

    @property
    def notional(self):
        if self.size_usdt is None or self.leverage is None:
            return None
        return self.size_usdt * self.leverage

    @property
    def risk_usdt(self):
        """這筆交易在停損處會實際虧掉多少 USDT。"""
        if self.notional is None:
            return None
        return self.notional * self.intent.stop_distance_pct / 100

    def to_dict(self):
        return _serialize(self)


# ---------------- 合約規則 ----------------

@dataclass
class TradingRules:
    """
    單一合約的交易規則。**必須從交易所動態取得,不可寫死。**

    (exchange, market_type, symbol) 是唯一鍵 ——
    Standard 與 Perpetual 的同一個 symbol 規則不同。
    """
    exchange: str
    market_type: MarketType
    symbol: str
    tick_size: Optional[float] = None
    step_size: Optional[float] = None
    min_qty: Optional[float] = None
    max_qty: Optional[float] = None
    min_notional: Optional[float] = None
    contract_size: Optional[float] = None
    price_precision: Optional[int] = None
    qty_precision: Optional[int] = None
    max_leverage: Optional[float] = None
    fetched_at: Optional[datetime] = None

    def __post_init__(self):
        self.market_type = MarketType.parse(self.market_type, MarketType.PERPETUAL)

    @property
    def key(self):
        return (self.exchange, self.market_type.value, self.symbol)

    def to_dict(self):
        return _serialize(self)


# ---------------- 訂單與部位 ----------------

@dataclass
class Order:
    client_order_id: str
    symbol: str
    market_type: MarketType
    side: OrderSide
    order_type: OrderType
    quantity: float
    state: OrderState = OrderState.CREATED
    price: Optional[float] = None
    exchange_order_id: Optional[str] = None
    filled_quantity: float = 0.0
    average_fill_price: Optional[float] = None
    reduce_only: bool = False
    reject_reason: Optional[str] = None
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: Optional[datetime] = None

    def __post_init__(self):
        self.market_type = MarketType.parse(self.market_type, MarketType.PERPETUAL)
        self.side = OrderSide.parse(self.side, OrderSide.BUY)
        self.order_type = OrderType.parse(self.order_type, OrderType.MARKET)
        self.state = OrderState.parse(self.state, OrderState.CREATED)

    @property
    def remaining_quantity(self):
        return max(0.0, self.quantity - self.filled_quantity)

    @property
    def needs_reconciliation(self):
        return self.state.needs_reconciliation

    def to_dict(self):
        return _serialize(self)


@dataclass
class Position:
    """
    未平倉部位。

    size_usdt 是**保證金**,名目價值 = size_usdt × leverage。
    這個區分很重要 —— 舊系統把兩者混用,導致已實現與未實現損益差了 N 倍。
    """
    symbol: str
    market_type: MarketType
    direction: Direction
    entry_price: float
    size_usdt: float
    leverage: float = 1.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    trade_id: Optional[int] = None
    source: str = "MANUAL"
    opened_at: Optional[datetime] = None

    def __post_init__(self):
        self.direction = _as_direction(self.direction)
        self.market_type = MarketType.parse(self.market_type, MarketType.PERPETUAL)
        self.entry_price = float(self.entry_price)
        self.size_usdt = float(self.size_usdt)
        self.leverage = float(self.leverage or 1.0)

    @property
    def notional(self):
        return self.size_usdt * self.leverage

    @property
    def is_protected(self):
        """沒有停損的部位等於裸奔,監控層必須把它當成異常回報。"""
        return self.stop_loss is not None

    def unrealized_pnl(self, current_price):
        """
        未實現損益(USDT)。與已實現損益用同一套公式:
            pnl = 保證金 × 價格變動比例 × 槓桿
        """
        if current_price is None or self.entry_price <= 0:
            return None
        change = self.direction.price_change_pct(self.entry_price, current_price)
        return self.size_usdt * change * self.leverage

    def roi_pct(self, current_price):
        """保證金 ROI(%)。"""
        if current_price is None or self.entry_price <= 0:
            return None
        change = self.direction.price_change_pct(self.entry_price, current_price)
        return change * self.leverage * 100

    def to_dict(self):
        return _serialize(self)


@dataclass
class ClosedTrade:
    """
    已平倉交易。

    pnl_basis 記錄損益的計算基準:
      - LEVERAGED           Phase 0.5 之後,已實現損益已乘槓桿
      - LEGACY_UNLEVERAGED  Phase 0.5 之前寫入的資料,損益漏乘槓桿
    歷史資料一律保留原值並標記,不回頭改寫任何歷史績效數字。
    """
    symbol: str
    direction: Direction
    entry_price: float
    exit_price: float
    size_usdt: float
    leverage: float
    pnl_usdt: float
    pnl_pct: float
    close_reason: Optional[str] = None
    pnl_basis: str = "LEVERAGED"
    trade_id: Optional[int] = None
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    def __post_init__(self):
        self.direction = _as_direction(self.direction)

    @property
    def is_win(self):
        return self.pnl_usdt > 0

    @property
    def pnl_is_comparable(self):
        """
        False 代表這筆的損益是在舊的錯誤基準下算的,
        不應該與新資料放在同一個績效統計裡直接比較。
        """
        return self.pnl_basis == "LEVERAGED"

    def to_dict(self):
        return _serialize(self)


# ---------------- 序列化 ----------------

def _serialize(obj):
    """dataclass -> 可 JSON 化的 dict(Enum 轉 value,datetime 轉 ISO 字串)。"""
    def convert(value):
        if isinstance(value, datetime):
            return value.isoformat()
        if hasattr(value, "value") and type(value).__name__.endswith(
            ("Direction", "MarketType", "OrderSide", "OrderType",
             "OrderState", "PositionSide", "TradingMode", "ConfidenceBand",
             "StrategyStatus")
        ):
            return value.value
        return value

    def walk(value):
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        return convert(value)

    return walk(asdict(obj))
