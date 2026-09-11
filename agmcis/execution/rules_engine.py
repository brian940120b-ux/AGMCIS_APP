"""
Trading Rules Engine(Master Prompt 第 12 條)。

位置:
    Signal -> Risk Engine -> **Trading Rules Engine** -> Execution Engine -> Exchange

職責分界很重要:
    Risk Engine        決定「要不要開、開多大、幾倍槓桿」  ← 風險判斷
    Trading Rules      決定「交易所收不收這組數字」          ← 合約規則
    Execution Engine   真的送出去並管狀態機                  ← 執行

這一層**不做風險判斷**。它不知道也不該知道帳戶回撤多少、今天虧了多少。
它只回答一個問題:這張單送出去,交易所會不會因為格式問題拒絕?

設計原則:
  1. **一次回報所有問題**。下單被拒之後一次修好,比來回試三次好。
  2. **能修的就修,不能修的才拒絕**。數量不是 step 的倍數 -> 自動進位;
     數量低於最小量 -> 拒絕(硬補上去等於偷改風控決定的倉位大小)。
  3. **永遠不放寬風控的決定**。進位只會讓倉位變小,不會變大。
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from agmcis.core.enums import Direction, MarketType, OrderSide, OrderType, PositionSide
from agmcis.core.errors import TradingRuleViolation
from agmcis.core.models import TradeIntent, TradingRules
from agmcis.execution import client_order_id as coid
from agmcis.exchange import trading_rules as tr

logger = logging.getLogger("agmcis.rules_engine")


@dataclass
class TradingContext:
    """
    驗證需要的帳戶與市場狀態。由呼叫端提供,讓這一層保持可測試。

    刻意用明確的資料結構而不是「引擎自己去查」——
    引擎自己查會讓每次驗證都打一次 API,而且沒辦法測。
    """
    available_balance: Optional[float] = None
    position_mode: str = "oneway"          # oneway | hedge
    margin_mode: Optional[str] = None      # cross | isolated
    open_symbols: List[str] = field(default_factory=list)
    pending_client_order_ids: List[str] = field(default_factory=list)
    supported_order_types: Optional[List[str]] = None

    @property
    def is_hedge_mode(self):
        return str(self.position_mode).lower() in ("hedge", "dual", "both")


@dataclass
class OrderRequest:
    """驗證通過、可以送給交易所的訂單。"""
    client_order_id: str
    symbol: str
    market_type: MarketType
    side: OrderSide
    position_side: PositionSide
    order_type: OrderType
    quantity: float
    # 送給交易所的限價。市價單是 None —— 這是對的,市價單不該帶價格。
    price: Optional[float] = None
    # 當下用來換算數量的參考價。市價單也有,用來算名目與記錄決策當時的價格。
    reference_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    leverage: Optional[float] = None
    size_usdt: Optional[float] = None
    reduce_only: bool = False
    adjustments: List[str] = field(default_factory=list)

    @property
    def notional(self):
        """名目價值。市價單用參考價計算,所以這裡不會是 None。"""
        price = self.price if self.price is not None else self.reference_price
        if price is None:
            return None
        return self.quantity * price

    def to_dict(self):
        return {
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "market_type": self.market_type.value,
            "side": self.side.value,
            "position_side": self.position_side.value,
            "order_type": self.order_type.value,
            "quantity": self.quantity,
            "price": self.price,
            "reference_price": self.reference_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "leverage": self.leverage,
            "size_usdt": self.size_usdt,
            "reduce_only": self.reduce_only,
            "adjustments": list(self.adjustments),
            "notional": self.notional,
        }


@dataclass
class ValidationResult:
    ok: bool
    order: Optional[OrderRequest] = None
    violations: List[str] = field(default_factory=list)
    adjustments: List[str] = field(default_factory=list)

    @property
    def reason(self):
        return "; ".join(self.violations) if self.violations else None

    def raise_if_invalid(self):
        if not self.ok:
            raise TradingRuleViolation("trading_rules", self.reason)
        return self.order


class TradingRulesEngine:
    def __init__(self, registry=None):
        self._registry = registry

    @property
    def registry(self):
        if self._registry is None:
            self._registry = tr.get_registry()
        return self._registry

    # ---------------- 主入口 ----------------

    def validate(self, intent: TradeIntent, size_usdt, leverage,
                 price=None, order_type=OrderType.MARKET,
                 context: Optional[TradingContext] = None,
                 reduce_only=False, client_order_id=None) -> ValidationResult:
        """
        把「風控批准的意圖」轉成「交易所收得下的訂單」。

        size_usdt 是保證金,名目價值 = size_usdt × leverage。
        """
        context = context or TradingContext()
        violations = []
        adjustments = []

        # ---- 1. 合約是否存在,規則是否拿得到 ----
        try:
            rules = self.registry.get(intent.symbol, intent.market_type)
        except Exception as exc:
            return ValidationResult(
                ok=False,
                violations=[f"{intent.symbol} 取不到合約規則: {exc}"],
            )

        # ---- 2. 方向與 side ----
        if not intent.direction.is_directional:
            violations.append(f"方向必須是明確的多或空,收到 {intent.direction.value}")

        side = intent.order_side
        position_side = self._position_side(intent.direction, context)

        # ---- 3. 訂單型別 ----
        order_type = OrderType.parse(order_type, OrderType.MARKET)
        if context.supported_order_types is not None:
            supported = {str(t).lower() for t in context.supported_order_types}
            if order_type.value not in supported:
                violations.append(
                    f"交易所不支援 {order_type.value} 訂單,可用:{sorted(supported)}"
                )

        # ---- 4. 價格 ----
        reference_price = price if price is not None else intent.entry

        if reference_price is None or reference_price <= 0:
            violations.append(f"參考價格無效: {reference_price}")
            return ValidationResult(ok=False, violations=violations)

        limit_price = None
        if order_type in (OrderType.LIMIT, OrderType.STOP, OrderType.TAKE_PROFIT):
            limit_price = tr.round_price(reference_price, rules, intent.direction, is_stop=False)
            if limit_price != reference_price:
                adjustments.append(
                    f"限價 {reference_price} 進位到 tick_size -> {limit_price}"
                )
            ok, problems = tr.validate_price(limit_price, rules)
            violations.extend(problems)

        # ---- 5. 槓桿 ----
        ok, problems = tr.validate_leverage(leverage, rules)
        violations.extend(problems)

        # ---- 6. 數量:由名目換算,並進位到合約單位 ----
        if size_usdt is None or size_usdt <= 0:
            violations.append(f"保證金必須大於 0,收到 {size_usdt}")
            return ValidationResult(ok=False, violations=violations)

        notional = float(size_usdt) * float(leverage or 1)
        quantity, problems = tr.quantity_for_notional(notional, reference_price, rules)

        if quantity is None:
            violations.extend(problems)
        else:
            raw = notional / (reference_price * (rules.contract_size or 1))
            if abs(raw - quantity) > 1e-12:
                adjustments.append(
                    f"數量 {raw:.10g} 無條件捨去到 step_size -> {quantity}"
                )

        # ---- 7. 保證金是否足夠 ----
        if context.available_balance is not None and size_usdt > context.available_balance:
            violations.append(
                f"保證金 {size_usdt} 超過可用餘額 {context.available_balance}"
            )

        # ---- 8. 停損停利:進位並確認方向仍然正確 ----
        stop_loss = tr.round_price(intent.stop_loss, rules, intent.direction, is_stop=True)
        if stop_loss != intent.stop_loss:
            adjustments.append(f"停損 {intent.stop_loss} 進位到 tick_size -> {stop_loss}")

        if not self._on_safe_side(intent.direction, reference_price, stop_loss, is_stop=True):
            violations.append(
                f"停損 {stop_loss} 進位後方向不正確"
                f"({intent.direction.value} 進場 {reference_price})"
            )

        take_profit = None
        if intent.take_profit is not None:
            take_profit = tr.round_price(
                intent.take_profit, rules, intent.direction, is_stop=False
            )
            if take_profit != intent.take_profit:
                adjustments.append(
                    f"停利 {intent.take_profit} 進位到 tick_size -> {take_profit}"
                )
            if not self._on_safe_side(
                intent.direction, reference_price, take_profit, is_stop=False
            ):
                violations.append(f"停利 {take_profit} 進位後方向不正確")

        # ---- 9. 重複倉位 / 重複訂單 ----
        if not reduce_only and intent.symbol in (context.open_symbols or []):
            violations.append(f"{intent.symbol} 已有未平倉位,不重複開倉")

        order_id = client_order_id or coid.generate(intent.symbol)
        if not coid.is_valid(order_id):
            violations.append(f"client order id 不合法: {order_id!r}")
        if order_id in (context.pending_client_order_ids or []):
            violations.append(f"client order id 重複: {order_id}")

        # ---- 10. reduce only 只能用在已有部位上 ----
        if reduce_only and intent.symbol not in (context.open_symbols or []):
            violations.append(f"{intent.symbol} 沒有部位,不能送 reduce-only 單")

        if violations:
            logger.info(
                "Trading Rules | REJECT | %s | %s", intent.symbol, "; ".join(violations)
            )
            return ValidationResult(
                ok=False, violations=violations, adjustments=adjustments
            )

        order = OrderRequest(
            client_order_id=order_id,
            symbol=rules.symbol,
            market_type=intent.market_type,
            side=side,
            position_side=position_side,
            order_type=order_type,
            quantity=quantity,
            price=limit_price,
            reference_price=float(reference_price),
            stop_loss=stop_loss,
            take_profit=take_profit,
            leverage=float(leverage) if leverage else None,
            size_usdt=float(size_usdt),
            reduce_only=reduce_only,
            adjustments=adjustments,
        )

        if adjustments:
            logger.info(
                "Trading Rules | ACCEPT (已調整) | %s | %s",
                intent.symbol, "; ".join(adjustments),
            )

        return ValidationResult(ok=True, order=order, adjustments=adjustments)

    # ---------------- 內部 ----------------

    @staticmethod
    def _position_side(direction, context):
        """
        One-Way 模式下 positionSide 是 BOTH;Hedge 模式要明確指定 LONG / SHORT。
        搞錯會讓平倉單變成反向開倉。
        """
        if not context.is_hedge_mode:
            return PositionSide.BOTH
        return (
            PositionSide.LONG if direction is Direction.LONG else PositionSide.SHORT
        )

    @staticmethod
    def _on_safe_side(direction, entry, price, is_stop):
        if price is None or entry is None or price <= 0:
            return False
        below = price < entry
        if direction is Direction.LONG:
            return below if is_stop else not below
        return (not below) if is_stop else below


_engine = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = TradingRulesEngine()
    return _engine


def set_engine(engine):
    global _engine
    _engine = engine
