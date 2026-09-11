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
from agmcis.execution import emergency
from agmcis.execution.broker import PaperBroker
from agmcis.execution.rules_engine import TradingContext, get_engine as get_rules_engine
from agmcis.execution import order_store as order_store_module

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
    partial: bool = False
    unfilled_quantity: float = 0.0
    protection: Optional[object] = None

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
            "partial": self.partial,
            "unfilled_quantity": self.unfilled_quantity,
            "protection": self.protection.to_dict() if self.protection else None,
        }


# 執行結果的狀態碼
OPENED = "OPENED"
REJECTED_BY_RULES = "REJECTED_BY_RULES"
REJECTED_NOT_APPROVED = "REJECTED_NOT_APPROVED"
SUBMIT_FAILED = "SUBMIT_FAILED"
NAKED_POSITION_CLOSED = "NAKED_POSITION_CLOSED"
NAKED_POSITION_STUCK = "NAKED_POSITION_STUCK"
OPENED_PARTIAL = "OPENED_PARTIAL"
PARTIAL_REMAINDER_STUCK = "PARTIAL_REMAINDER_STUCK"
CLOSED = "CLOSED"
CLOSE_FAILED = "CLOSE_FAILED"


class ExecutionEngine:

    def __init__(self, broker=None, rules_engine=None, store=None,
                 persist=True, pause_file=None):
        self._broker = broker
        self._rules_engine = rules_engine
        self._store = store
        # persist=False 只給不需要資料庫的測試用。
        # 生產環境**必須**持久化 —— 沒有持久化就沒有對帳,
        # 沒有對帳就不能重試,而不能重試的執行層遇到逾時只能放著。
        self._persist = persist
        # 緊急保護停新單時要寫的旗標檔。None = 用全域設定。
        # 測試必須注入,否則會在專案根目錄留下一個真的會擋交易的檔案。
        self._pause_file = pause_file

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

    @property
    def store(self):
        if self._store is None:
            self._store = order_store_module.get_store()
        return self._store

    # ---------------- 狀態轉移 + 持久化 ----------------

    def _move(self, order, target, reason=None):
        """
        狀態轉移一律走這裡,這樣每一步都會落地。

        寫不進資料庫時**不會**把狀態改掉 —— 記憶體說 FILLED、
        資料庫說 SUBMITTING,對帳就會拿到互相矛盾的兩份事實。
        """
        previous = order.state
        sm.transition(order, target, reason=reason)

        if not self._persist:
            return order

        try:
            self.store.save(order)
            self.store.record_event(
                order.client_order_id, previous, order.state, reason,
            )
        except Exception as exc:
            # 記錄失敗不該讓已經送出去的單消失,但必須大聲說 ——
            # 這時資料庫的狀態已經落後於真實狀態了。
            logger.critical(
                "Execution | ORDER_PERSIST_FAILED | %s | %s -> %s | %s | "
                "資料庫狀態已落後,對帳時會需要處理",
                order.client_order_id, previous.value, order.state.value, exc,
            )

        return order

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

        self._move(order, OrderState.VALIDATING)
        self._move(order, OrderState.RISK_CHECK)
        self._move(order, OrderState.SUBMITTING)

        # ---- 2. 送單 ----
        try:
            fill = self.broker.submit_entry(
                request, intent, decision.size_usdt, decision.leverage,
            )
        except Exception as exc:
            # 送單過程炸掉時,系統**不知道**交易所收到了什麼。
            # 標記成 UNKNOWN 等對帳,不可以直接重送。
            logger.exception("Execution | SUBMIT_EXCEPTION | %s", symbol)
            self._move(order, OrderState.UNKNOWN, reason=str(exc))
            return ExecutionResult(
                ok=False, order=order, symbol=symbol, status=SUBMIT_FAILED,
                reason=f"送單例外,狀態未知,需對帳:{type(exc).__name__}: {exc}",
            )

        if not fill.is_filled:
            self._move(order, OrderState.REJECTED, reason=fill.reason)
            logger.info("Execution | SUBMIT_REJECTED | %s | %s", symbol, fill.reason)
            return ExecutionResult(
                ok=False, order=order, symbol=symbol, status=SUBMIT_FAILED,
                reason=fill.reason,
            )

        order.exchange_order_id = fill.exchange_order_id
        order.filled_quantity = fill.filled_quantity
        order.average_fill_price = fill.average_price

        self._move(order, OrderState.ACCEPTED)

        # ---- 3. 部分成交 ----
        partial = fill.is_partial()

        if partial:
            # 部分成交的部位**一樣是真的部位**,一樣需要停損。
            # 而未成交的剩餘量不能留著沒人管 —— 它可能稍後才成交,
            # 那時候沒有任何人在看它。
            logger.warning(
                "Execution | PARTIAL_FILL | %s | 送出 %s 成交 %s(未成交 %s)",
                symbol, fill.requested_quantity, fill.filled_quantity,
                fill.unfilled_quantity,
            )
            self._move(order, OrderState.PARTIALLY_FILLED,
                       reason=f"部分成交,未成交 {fill.unfilled_quantity}")

            cancelled = self._cancel_remainder(order, symbol)
            if not cancelled:
                # 撤不掉的掛單是一個會自己長大的部位。這比裸倉更難處理,
                # 因為連「現在有多少部位」都不確定。
                logger.critical(
                    "Execution | PARTIAL_REMAINDER_STUCK | %s | "
                    "未成交 %s 撤不掉,可能稍後成交且無人監控 | 需要人工介入",
                    symbol, fill.unfilled_quantity,
                )
                return ExecutionResult(
                    ok=False, order=order, symbol=symbol,
                    status=PARTIAL_REMAINDER_STUCK,
                    reason=f"部分成交且剩餘量撤不掉({fill.unfilled_quantity}),"
                           f"需要人工處理",
                    partial=True,
                    unfilled_quantity=fill.unfilled_quantity,
                )

        self._move(order, OrderState.FILLED)

        # ---- 4. 保護性停損:這一步不成立就不准留著這個部位 ----
        result = self._ensure_protected(order, intent, validation)
        result.partial = partial
        result.unfilled_quantity = fill.unfilled_quantity if partial else 0.0

        if partial and result.ok:
            result.status = OPENED_PARTIAL
            result.reason = (
                f"部分成交:送出 {fill.requested_quantity} 成交 "
                f"{fill.filled_quantity},剩餘量已撤銷"
            )

        return result

    def _cancel_remainder(self, order, symbol):
        """
        撤掉未成交的剩餘量。撤不掉回 False。

        broker 沒有實作這個方法時視為**撤不掉** —— 假設它成功會讓
        一張還活著的掛單靜靜地留在市場上。
        """
        try:
            return bool(self.broker.cancel_remainder(order))
        except NotImplementedError:
            logger.error(
                "Execution | CANCEL_UNSUPPORTED | %s | "
                "broker 沒有實作 cancel_remainder,無法確認剩餘量已撤銷",
                symbol,
            )
            return False
        except Exception as exc:
            logger.exception("Execution | CANCEL_FAILED | %s", symbol)
            return False

    def _ensure_protected(self, order, intent, validation):
        """
        成交之後確認停損存在。沒有就跑第十八節的六步驟緊急保護。

        在 Phase 18 以前這裡是「檢查一次、沒有就立刻平倉」。那是安全的,
        但把一次網路抖動和「這個部位不可能有停損」當成同一件事 ——
        重試一次就能救回來的部位被平掉,每次都要付一趟手續費與滑點。

        現在由 emergency.protect() 決定結局。這一層只負責把結局
        翻譯成狀態機轉移與 ExecutionResult。
        """
        symbol = intent.symbol

        outcome = emergency.protect(
            self.broker, symbol, intent.stop_loss,
            close_position=lambda sym, reason: self._emergency_close(
                order, sym, reason,
            ),
            pause_file=self._pause_file,
        )

        if outcome.protected:
            self._move(order, OrderState.PROTECTED)
            logger.info(
                "Execution | OPENED | %s | %s | qty=%s @ %s | 停損 %s | %s",
                symbol, order.side.value, order.filled_quantity,
                order.average_fill_price, intent.stop_loss, outcome.status,
            )
            adjustments = list(validation.adjustments)
            if outcome.status != emergency.PROTECTED:
                adjustments.append(f"緊急保護:{outcome.status}")
            if outcome.reduced_fraction:
                adjustments.append(f"緊急縮倉 {outcome.reduced_fraction:.0%}")
            return ExecutionResult(
                ok=True, order=order, symbol=symbol, status=OPENED,
                adjustments=adjustments,
                fill_price=order.average_fill_price,
                protection=outcome,
            )

        if outcome.status == emergency.CLOSED:
            # CLOSING 已經在 _emergency_close() 裡推過了
            self._move(order, OrderState.CLOSED, reason="裸倉緊急平倉")
            return ExecutionResult(
                ok=False, order=order, symbol=symbol,
                status=NAKED_POSITION_CLOSED,
                reason="開倉後掛不上停損,已緊急平倉",
                naked_position_closed=True,
                protection=outcome,
            )

        # 最糟的情況:有裸倉、救不回來、又平不掉。這必須大聲喊。
        logger.critical(
            "Execution | NAKED_POSITION_STUCK | %s | %s | 需要人工介入",
            symbol, outcome.reason,
        )
        self._move(order, OrderState.FAILED, reason=outcome.reason or "裸倉平倉失敗")
        return ExecutionResult(
            ok=False, order=order, symbol=symbol, status=NAKED_POSITION_STUCK,
            reason=f"裸倉且平倉失敗,需要人工處理:{outcome.reason}",
            protection=outcome,
        )

    def _emergency_close(self, order, symbol, reason):
        """
        給緊急保護用的平倉。回傳 bool —— 那一層只需要知道成不成功。

        先推 CLOSING 再送單:FILLED 不能直接跳到 CLOSED,而且
        「已經開始平了」這件事必須在送單**之前**就留下紀錄 ——
        送單當下斷線的話,CLOSING 是唯一能讓對帳知道要去查什麼的線索。
        """
        if order.state is not OrderState.CLOSING:
            self._move(order, OrderState.CLOSING, reason=reason)

        try:
            result = self.broker.close_position(symbol, reason=reason)
        except Exception:
            logger.exception("Execution | NAKED_CLOSE_EXCEPTION | %s", symbol)
            return False
        return bool(result.ok)

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
