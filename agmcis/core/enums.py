"""
系統列舉型別。

Direction 取代原本散落在 12 個以上模組裡、直接比對的中文字串 "做多" / "做空"。
舊資料與舊呼叫端仍然大量使用中文字串,所以每個列舉都提供 parse() 接受各種寫法,
而 value 維持中文 —— 資料庫的 signal 欄位存的就是中文,不能在 Phase 1 就換掉。
"""
from enum import Enum


class _ParsableEnum(Enum):
    @classmethod
    def parse(cls, raw, default=None):
        """寬鬆解析。認不出來時回傳 default(預設 None),絕不猜。"""
        if isinstance(raw, cls):
            return raw
        if raw is None:
            return default

        text = str(raw).strip()
        if not text:
            return default

        for member in cls:
            if text == member.value:
                return member

        lowered = text.lower()
        for member in cls:
            if lowered in member.aliases:
                return member

        return default

    @property
    def aliases(self):
        return frozenset()


class Direction(_ParsableEnum):
    """
    交易方向。value 沿用資料庫裡的中文字串,不在 Phase 1 動 schema。
    """
    LONG = "做多"
    SHORT = "做空"
    WAIT = "觀望"

    @property
    def aliases(self):
        return {
            Direction.LONG: frozenset({"long", "buy", "多", "bull", "bullish"}),
            Direction.SHORT: frozenset({"short", "sell", "空", "bear", "bearish"}),
            Direction.WAIT: frozenset({"wait", "hold", "none", "neutral", "flat", "觀察"}),
        }[self]

    @property
    def is_directional(self):
        return self in (Direction.LONG, Direction.SHORT)

    @property
    def opposite(self):
        if self is Direction.LONG:
            return Direction.SHORT
        if self is Direction.SHORT:
            return Direction.LONG
        return Direction.WAIT

    @property
    def order_side(self):
        """開倉時要送給交易所的 side。"""
        return {
            Direction.LONG: OrderSide.BUY,
            Direction.SHORT: OrderSide.SELL,
        }.get(self)

    def price_change_pct(self, entry_price, exit_price):
        """
        價格變動比例(小數),已依方向取正負號。
        這一層不含槓桿 —— 槓桿只在換算 ROI 與 USDT 損益時才乘上去。
        """
        entry_price = float(entry_price)
        exit_price = float(exit_price)

        if entry_price <= 0:
            raise ValueError(f"entry_price 必須大於 0,收到 {entry_price}")

        if self is Direction.LONG:
            return (exit_price - entry_price) / entry_price
        if self is Direction.SHORT:
            return (entry_price - exit_price) / entry_price

        raise ValueError(f"{self.value} 沒有方向,無法計算損益")


class MarketType(_ParsableEnum):
    """
    BingX 的兩種合約市場。必須明確區分 —— 兩者的 contract_size、tick_size、
    step_size、min_notional 與槓桿上限都不同,混在一起會算出錯誤的下單數量。
    """
    PERPETUAL = "perpetual"
    STANDARD = "standard"

    @property
    def aliases(self):
        return {
            MarketType.PERPETUAL: frozenset({"swap", "perp", "usdt-m", "永續"}),
            MarketType.STANDARD: frozenset({"futures", "標準"}),
        }[self]


class OrderSide(_ParsableEnum):
    BUY = "buy"
    SELL = "sell"


class PositionSide(_ParsableEnum):
    LONG = "long"
    SHORT = "short"
    BOTH = "both"


class OrderType(_ParsableEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_MARKET = "stop_market"
    TAKE_PROFIT = "take_profit"
    TAKE_PROFIT_MARKET = "take_profit_market"
    TRAILING_STOP = "trailing_stop"


class OrderState(_ParsableEnum):
    """
    訂單狀態機。

    正常流程:
        CREATED -> VALIDATING -> RISK_CHECK -> SUBMITTING -> ACCEPTED
                -> PARTIALLY_FILLED -> FILLED -> PROTECTED -> CLOSING -> CLOSED
    """
    CREATED = "created"
    VALIDATING = "validating"
    RISK_CHECK = "risk_check"
    SUBMITTING = "submitting"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    PROTECTED = "protected"
    CLOSING = "closing"
    CLOSED = "closed"

    # 異常
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"
    FAILED = "failed"

    @property
    def is_terminal(self):
        return self in (
            OrderState.CLOSED, OrderState.REJECTED, OrderState.CANCELLED,
            OrderState.EXPIRED, OrderState.FAILED,
        )

    @property
    def needs_reconciliation(self):
        """
        這些狀態下系統不知道交易所到底收到什麼,
        **絕對不可以直接重送** —— 必須先查詢交易所再決定。
        """
        return self in (OrderState.UNKNOWN, OrderState.TIMEOUT, OrderState.SUBMITTING)


class TradingMode(_ParsableEnum):
    MANUAL = "manual"
    PAPER = "paper"
    TEST = "test"
    LIVE = "live"

    @property
    def sends_real_orders(self):
        return self in (TradingMode.TEST, TradingMode.LIVE)

    @property
    def risks_real_money(self):
        return self is TradingMode.LIVE


class StrategyStatus(_ParsableEnum):
    """策略生命週期。只有 LIVE 的策略可以影響真實資金。"""
    RESEARCH = "research"
    VALIDATION = "validation"
    PAPER = "paper"
    APPROVED = "approved"
    LIVE = "live"
    PAUSED = "paused"
    RETIRED = "retired"


class ConfidenceBand(_ParsableEnum):
    """
    信心分級。門檻本身必須由回測驗證,不要當成既定事實 ——
    目前的數字只是分類標籤,不是「這個區間就會賺」的保證。
    """
    EXTREME = "extreme"
    VERY_HIGH = "very_high"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NO_TRADE = "no_trade"

    @classmethod
    def of(cls, confidence):
        """confidence 為 None(資料異常)時回傳 NO_TRADE,不當成中性。"""
        if confidence is None:
            return cls.NO_TRADE
        value = float(confidence)
        if value >= 90:
            return cls.EXTREME
        if value >= 80:
            return cls.VERY_HIGH
        if value >= 70:
            return cls.HIGH
        if value >= 60:
            return cls.MEDIUM
        if value >= 50:
            return cls.LOW
        return cls.NO_TRADE
