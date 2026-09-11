"""
Execution Engine。

位置:

    Agents -> TradeIntent -> Risk Engine -> Trading Rules -> **Execution Engine** -> Broker

這一層**不做任何判斷**。它不決定要不要交易(Agent + Risk Engine 的事),
不決定倉位大小(Risk Engine 的事),不決定交易所收不收(Trading Rules 的事)。
它只負責一件事:**把批准的決定安全地送出去,並且知道現在到哪一步了。**

## 最重要的一條:裸倉

開倉成功、但停損沒有掛上去 = **裸倉**。部位已經存在,但沒有虧損上限。

那不是「待辦事項」,是必須**立刻**處理的狀態。這個引擎的做法是:
成交後檢查保護,沒有保護就**馬上平掉**,而且用 ERROR 級別記錄下來。

寧可平掉一個可能會賺的倉位,也不要留一個沒有停損的倉位過夜。
這條規則沒有例外,也沒有設定可以關掉它。

## 不做的事

  * **不重送需要對帳的訂單。** SUBMITTING / TIMEOUT / UNKNOWN 代表系統
    不知道交易所收到了什麼,直接重送可能開出第二張倉位。
  * **不接受未經批准的 intent。** approved=False 的裁決直接拒絕執行。
  * **不自己算倉位大小。** size 與 leverage 只能從 RiskDecision 來。
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from agmcis.core.enums import OrderState, OrderType
from agmcis.core.errors import TradingRuleViolation
from agmcis.core.models import Order
from agmcis.execution import state_machine as sm
from agmcis.execution.broker import PaperBroker
from agmcis.execution.rules_engine import TradingContext, get_engine as get_rules_engine

logger = logging.getLogger("agmcis.execution.engine")


@dataclass
class ExecutionResult:
    ok: bool
    order: Optional[Order] = None
    symbol: Optional[str] = None
    status: str = ""
    reason: Optional[str] = None
    violations: List[str] = field(default_factory=list)
    adjustments: List[str] = field(default_factory=list)
    naked_position_closed: bool = False
    fill_price: Optional[float] = None

    def to_dict(self):
        return {
            "ok": self.ok,
            "symbol": self.symbol,
            "status": self.status,
            "state": self.order.state.value if self.order else None,
            "client_order_id": self.order.client_order_id if self.order else None,
            "reason": self.reason,
            "violations": list(self.violations),
            "adjustments": list(self.adjustments),
            "naked_position_closed": self.naked_position_closed,
            "fill_price": self.fill_price,
        }


# 執行結果的狀態碼
OPENED = "OPENED"
REJECTED_BY_RULES = "REJECTED_BY_RULES"
REJECTED_NOT_APPROVED = "REJECTED_NOT_APPROVED"
SUBMIT_FAILED = "SUBMIT_FAILED"
NAKED_POSITION_CLOSED = "NAKED_POSITION_CLOSED"
NAKED_POSITION_STUCK = "NAKED_POSITION_STUCK"
CLOSED = "CLOSED"
CLOSE_FAILED = "CLOSE_FAILED"


class ExecutionEngine:

    def __init__(self, broker=None, rules_engine=None):
        self._broker = broker
        self._rules_engine = rules_engine

    @property
    def broker(self):
        if self._broker is None:
            self._broker = PaperBroker()
        return self._broker

    @property
    def rules_engine(self):
        if self._rules_engine is None:
            self._rules_engine = get_rules_engine()
        return self._rules_engine

    # ---------------- 開倉 ----------------

    def execute(self, decision, context=None, order_type=OrderType.MARKET):
        """
        decision: RiskDecision(必須 approved=True)

        回傳 ExecutionResult。任何一步失敗都會留下 order 的最終狀態,
        呼叫端據此知道停在哪裡 —— 而不是只拿到一個 False。
        """
        intent = decision.intent
        symbol = intent.symbol

        if not decision.approved:
            # 風控沒批准的東西,執行層不該有辦法送出去
            logger.warning(
                "Execution | NOT_APPROVED | %s | %s", symbol, decision.reason,
            )
            return ExecutionResult(
                ok=False, symbol=symbol, status=REJECTED_NOT_APPROVED,
                reason=decision.reason or "風控未批准",
            )

        if decision.size_usdt is None or decision.leverage is None:
            return ExecutionResult(
                ok=False, symbol=symbol, status=REJECTED_NOT_APPROVED,
                reason="裁決缺少 size 或 leverage,執行層不自己算",
            )

        # ---- 1. Trading Rules ----
        validation = self.rules_engine.validate(
            intent, decision.size_usdt, decision.leverage,
            order_type=order_type, context=context or TradingContext(),
        )

        if not validation.ok:
            logger.info(
                "Execution | RULES_REJECTED | %s | %s", symbol, validation.reason,
            )
            return ExecutionResult(
                ok=False, symbol=symbol, status=REJECTED_BY_RULES,
                reason=validation.reason, violations=list(validation.violations),
            )

        request = validation.order
        order = Order(
            client_order_id=request.client_order_id,
            symbol=symbol,
            market_type=intent.market_type,
            side=request.side,
            order_type=request.order_type,
            quantity=request.quantity,
            price=request.price,
        )

        sm.transition(order, OrderState.VALIDATING)
        sm.transition(order, OrderState.RISK_CHECK)
        sm.transition(order, OrderState.SUBMITTING)

        # ---- 2. 送單 ----
        try:
            fill = self.broker.submit_entry(
                request, intent, decision.size_usdt, decision.leverage,
            )
        except Exception as exc:
            # 送單過程炸掉時,系統**不知道**交易所收到了什麼。
            # 標記成 UNKNOWN 等對帳,不可以直接重送。
            logger.exception("Execution | SUBMIT_EXCEPTION | %s", symbol)
            sm.transition(order, OrderState.UNKNOWN, reason=str(exc))
            return ExecutionResult(
                ok=False, order=order, symbol=symbol, status=SUBMIT_FAILED,
                reason=f"送單例外,狀態未知,需對帳:{type(exc).__name__}: {exc}",
            )

        if not fill.is_filled:
            sm.transition(order, OrderState.REJECTED, reason=fill.reason)
            logger.info("Execution | SUBMIT_REJECTED | %s | %s", symbol, fill.reason)
            return ExecutionResult(
                ok=False, order=order, symbol=symbol, status=SUBMIT_FAILED,
                reason=fill.reason,
            )

        order.exchange_order_id = fill.exchange_order_id
        order.filled_quantity = fill.filled_quantity
        order.average_fill_price = fill.average_price

        sm.transition(order, OrderState.ACCEPTED)
        sm.transition(order, OrderState.FILLED)

        # ---- 3. 保護性停損:這一步不成立就不准留著這個部位 ----
        return self._ensure_protected(order, intent, validation)

    def _ensure_protected(self, order, intent, validation):
        symbol = intent.symbol

        try:
            protected = self.broker.has_protection(symbol)
        except Exception as exc:
            logger.exception("Execution | PROTECTION_CHECK_FAILED | %s", symbol)
            protected = False

        if protected:
            sm.transition(order, OrderState.PROTECTED)
            logger.info(
                "Execution | OPENED | %s | %s | qty=%s @ %s | 停損 %s",
                symbol, order.side.value, order.filled_quantity,
                order.average_fill_price, intent.stop_loss,
            )
            return ExecutionResult(
                ok=True, order=order, symbol=symbol, status=OPENED,
                adjustments=list(validation.adjustments),
                fill_price=order.average_fill_price,
            )

        # ---- 裸倉 ----
        logger.error(
            "Execution | NAKED_POSITION | %s | 已成交但沒有停損保護,立即平倉", symbol,
        )
        return self._close_naked(order, symbol)

    def _close_naked(self, order, symbol):
        """
        裸倉處理。寧可平掉一個可能會賺的倉位,也不要留一個沒有停損的倉位。
        """
        sm.transition(order, OrderState.CLOSING, reason="裸倉:沒有停損保護")

        try:
            result = self.broker.close_position(
                symbol, reason="裸倉緊急平倉(開倉後沒有停損保護)",
            )
        except Exception as exc:
            logger.exception("Execution | NAKED_CLOSE_EXCEPTION | %s", symbol)
            sm.transition(order, OrderState.UNKNOWN, reason=str(exc))
            return ExecutionResult(
                ok=False, order=order, symbol=symbol, status=NAKED_POSITION_STUCK,
                reason=f"裸倉且平倉失敗,需要人工處理:{type(exc).__name__}: {exc}",
            )

        if not result.ok:
            # 最糟的情況:有裸倉、又平不掉。這必須大聲喊。
            logger.critical(
                "Execution | NAKED_POSITION_STUCK | %s | 平倉失敗:%s | 需要人工介入",
                symbol, result.reason,
            )
            sm.transition(order, OrderState.FAILED, reason=result.reason)
            return ExecutionResult(
                ok=False, order=order, symbol=symbol, status=NAKED_POSITION_STUCK,
                reason=f"裸倉且平倉失敗,需要人工處理:{result.reason}",
            )

        sm.transition(order, OrderState.CLOSED)
        return ExecutionResult(
            ok=False, order=order, symbol=symbol, status=NAKED_POSITION_CLOSED,
            reason="開倉後沒有停損保護,已緊急平倉",
            naked_position_closed=True,
        )

    # ---------------- 平倉 ----------------

    def close(self, symbol, price=None, reason="策略出場"):
        """
        主動平倉。給 Exit Agent 與 Kill Switch 用。

        找不到部位時回傳 ok=False,**不是例外** ——
        另一條路徑已經平掉是很常見的情況,不是錯誤。
        """
        try:
            result = self.broker.close_position(symbol, price=price, reason=reason)
        except Exception as exc:
            logger.exception("Execution | CLOSE_EXCEPTION | %s", symbol)
            return ExecutionResult(
                ok=False, symbol=symbol, status=CLOSE_FAILED,
                reason=f"{type(exc).__name__}: {exc}",
            )

        if not result.ok:
            logger.info("Execution | CLOSE_FAILED | %s | %s", symbol, result.reason)
            return ExecutionResult(
                ok=False, symbol=symbol, status=CLOSE_FAILED, reason=result.reason,
            )

        logger.info(
            "Execution | CLOSED | %s | @ %s | %s",
            symbol, result.average_price, reason,
        )
        return ExecutionResult(
            ok=True, symbol=symbol, status=CLOSED,
            fill_price=result.average_price, reason=reason,
        )

    # ---------------- 裸倉巡檢 ----------------

    def sweep_naked_positions(self, positions):
        """
        掃描所有部位,把沒有停損的平掉。

        這是安全網,不是主要防線 —— 主要防線是開倉時的 `_ensure_protected`。
        但停損可能因為別的原因消失(手動改、對帳修正、資料庫問題),
        所以還是要定期掃。
        """
        closed = []
        stuck = []

        for position in positions:
            symbol = position.get("symbol")
            if position.get("stoploss") is not None:
                continue

            logger.error(
                "Execution | NAKED_POSITION_FOUND | %s | 巡檢發現沒有停損的部位", symbol,
            )

            result = self.close(symbol, reason="裸倉巡檢:沒有停損保護")
            (closed if result.ok else stuck).append(symbol)

            if not result.ok:
                logger.critical(
                    "Execution | NAKED_POSITION_STUCK | %s | %s | 需要人工介入",
                    symbol, result.reason,
                )

        return {"closed": closed, "stuck": stuck, "checked": len(positions)}


_ENGINE = None


def get_engine():
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = ExecutionEngine()
    return _ENGINE


def set_engine(engine):
    global _ENGINE
    _ENGINE = engine
