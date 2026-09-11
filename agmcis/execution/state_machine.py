"""
訂單狀態機。

為什麼需要一個明確的狀態機:非法的狀態轉移在交易系統裡不是「小 bug」。
把一張已經 FILLED 的單當成 CREATED 重送,就是憑空多一張倉位;
把 TIMEOUT 當成 FAILED 直接重送,可能變成兩張單 ——
而系統當下根本不知道交易所到底收到了什麼。

三條規則:

  1. **允許的轉移寫死在表裡。** 不在表裡就拋例外,不是印個警告繼續跑。
  2. **終止狀態不可再轉移。** CLOSED 之後沒有下一步。
  3. **需要對帳的狀態不可重送。** UNKNOWN / TIMEOUT / SUBMITTING 只能先查詢,
     查到結果才知道下一步。這條由 `can_resubmit()` 守著。
"""
import logging
from agmcis.core.enums import OrderState
from agmcis.core.errors import TradingRuleViolation

logger = logging.getLogger("agmcis.execution.state_machine")

S = OrderState

# 允許的轉移。沒有列出來的一律拒絕。
TRANSITIONS = {
    S.CREATED: {S.VALIDATING, S.REJECTED, S.CANCELLED},
    S.VALIDATING: {S.RISK_CHECK, S.REJECTED},
    S.RISK_CHECK: {S.SUBMITTING, S.REJECTED},

    # 送出之後可能什麼都不知道 —— TIMEOUT / UNKNOWN 是合法結果,不是錯誤
    S.SUBMITTING: {S.ACCEPTED, S.REJECTED, S.TIMEOUT, S.UNKNOWN, S.FAILED},

    S.ACCEPTED: {
        S.PARTIALLY_FILLED, S.FILLED, S.CANCELLED, S.EXPIRED,
        S.TIMEOUT, S.UNKNOWN,
    },
    S.PARTIALLY_FILLED: {S.FILLED, S.CANCELLED, S.CLOSING, S.UNKNOWN},

    # FILLED 之後**必須**掛上保護性停損才算數。掛不上就得馬上平掉。
    S.FILLED: {S.PROTECTED, S.CLOSING, S.FAILED, S.UNKNOWN},

    S.PROTECTED: {S.CLOSING, S.CLOSED, S.UNKNOWN},
    S.CLOSING: {S.CLOSED, S.FAILED, S.UNKNOWN},

    # 對帳之後可以回到任何一個已知狀態
    S.TIMEOUT: {S.ACCEPTED, S.FILLED, S.PARTIALLY_FILLED, S.REJECTED,
                S.CANCELLED, S.UNKNOWN, S.FAILED},
    S.UNKNOWN: {S.ACCEPTED, S.FILLED, S.PARTIALLY_FILLED, S.PROTECTED,
                S.REJECTED, S.CANCELLED, S.CLOSED, S.FAILED},

    # 終止狀態
    S.CLOSED: set(),
    S.REJECTED: set(),
    S.CANCELLED: set(),
    S.EXPIRED: set(),
    S.FAILED: set(),
}


class IllegalTransition(TradingRuleViolation):
    def __init__(self, current, target, detail=""):
        super().__init__(
            "order_state",
            f"不允許的狀態轉移:{current.value} -> {target.value}"
            + (f"({detail})" if detail else ""),
        )
        self.current = current
        self.target = target


def can_transition(current, target):
    current = OrderState.parse(current, OrderState.UNKNOWN)
    target = OrderState.parse(target, OrderState.UNKNOWN)
    return target in TRANSITIONS.get(current, set())


def transition(order, target, reason=None):
    """
    就地修改 order.state。不允許的轉移拋 IllegalTransition。

    不允許「同狀態轉移」—— 那通常代表呼叫端搞不清楚自己在哪一步,
    靜靜放過會把真正的問題藏起來。
    """
    current = order.state
    target = OrderState.parse(target, OrderState.UNKNOWN)

    if not can_transition(current, target):
        raise IllegalTransition(current, target, reason or "")

    order.state = target
    if reason:
        order.reject_reason = reason if target in (
            OrderState.REJECTED, OrderState.FAILED,
        ) else order.reject_reason

    logger.info(
        "Order %s | %s -> %s%s",
        order.client_order_id, current.value, target.value,
        f" | {reason}" if reason else "",
    )
    return order


def can_resubmit(order):
    """
    這張單可不可以重送。

    **需要對帳的狀態一律不可以。** UNKNOWN / TIMEOUT / SUBMITTING 代表
    系統不知道交易所收到了什麼 —— 直接重送可能開出第二張倉位。
    """
    return not order.state.needs_reconciliation and not order.state.is_terminal


def is_naked(order):
    """
    裸倉:已成交但沒有保護。

    這是整個執行層最危險的狀態 —— 部位已經存在,但沒有停損,
    也就是沒有虧損上限。發現裸倉必須立刻處理,不能等下一輪。
    """
    return order.state in (OrderState.FILLED, OrderState.PARTIALLY_FILLED)
