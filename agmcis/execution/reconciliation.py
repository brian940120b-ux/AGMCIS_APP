"""
對帳。

三個問題:

  1. **那些狀態不明的單,到底成交了沒?**
     `UNKNOWN` / `TIMEOUT` / `SUBMITTING` 代表系統不知道交易所收到了什麼。
     這些單**不可以重送**,只能去查。查到答案才知道下一步。

  2. **本地認為有的部位,交易所那邊真的有嗎?**
     兩邊不一致時,**交易所永遠是事實來源**。本地紀錄是系統對現實的看法,
     不是現實本身。

  3. **交易所那邊有、但本地不知道的部位呢?**
     這是最危險的一種:一個沒有人在管的倉位,沒有停損、不會被監控、
     也不在風控的計算裡。

鐵律:**對帳只更正本地紀錄,絕不下單。**

想「順手把多出來的倉位平掉」是很自然的念頭,但對帳跑在排程裡、
在資料不完整時也會跑。一個會自動平倉的對帳程式,在交易所 API 回傳空清單
的那一刻會把整個帳戶清空。所以它只回報,平倉留給人或留給明確的流程。
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List

from agmcis.core.enums import OrderState
from agmcis.execution import order_store as order_store_module
from agmcis.execution import state_machine as sm

logger = logging.getLogger("agmcis.execution.reconciliation")

# 差異類型
ORDER_RESOLVED = "ORDER_RESOLVED"
ORDER_STILL_UNKNOWN = "ORDER_STILL_UNKNOWN"
LOCAL_ONLY_POSITION = "LOCAL_ONLY_POSITION"
EXCHANGE_ONLY_POSITION = "EXCHANGE_ONLY_POSITION"
SIZE_MISMATCH = "SIZE_MISMATCH"
UNPROTECTED_POSITION = "UNPROTECTED_POSITION"

# 數量差異超過這個比例才算不一致。浮點與交易所的精度處理會有微小差距。
SIZE_TOLERANCE = 0.01


@dataclass
class Discrepancy:
    kind: str
    symbol: str = ""
    client_order_id: str = ""
    detail: str = ""
    local: Dict = field(default_factory=dict)
    remote: Dict = field(default_factory=dict)

    @property
    def is_critical(self):
        """需要人立刻看的差異。"""
        return self.kind in (
            EXCHANGE_ONLY_POSITION, UNPROTECTED_POSITION, ORDER_STILL_UNKNOWN,
        )

    def to_dict(self):
        return {
            "kind": self.kind,
            "symbol": self.symbol,
            "client_order_id": self.client_order_id,
            "detail": self.detail,
            "critical": self.is_critical,
            "local": dict(self.local),
            "remote": dict(self.remote),
        }


@dataclass
class ReconciliationReport:
    checked_orders: int = 0
    checked_positions: int = 0
    resolved: List[str] = field(default_factory=list)
    discrepancies: List[Discrepancy] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def critical(self):
        return [d for d in self.discrepancies if d.is_critical]

    @property
    def ok(self):
        return not self.discrepancies and not self.errors

    def to_dict(self):
        return {
            "ok": self.ok,
            "checked_orders": self.checked_orders,
            "checked_positions": self.checked_positions,
            "resolved": list(self.resolved),
            "discrepancy_count": len(self.discrepancies),
            "critical_count": len(self.critical),
            "discrepancies": [d.to_dict() for d in self.discrepancies],
            "errors": list(self.errors),
        }


class Reconciler:
    """
    `fetch_order` 與 `fetch_positions` 由呼叫端提供 ——
    模擬盤與實盤的來源不同,但對帳邏輯完全一樣。
    """

    def __init__(self, store=None, fetch_order=None, fetch_positions=None,
                 local_positions=None):
        self._store = store
        self._fetch_order = fetch_order
        self._fetch_positions = fetch_positions
        self._local_positions = local_positions

    @property
    def store(self):
        if self._store is None:
            self._store = order_store_module.get_store()
        return self._store

    def _local(self):
        if self._local_positions is not None:
            return self._local_positions()
        from database_service import get_open_trades
        return get_open_trades()

    # ---------------- 主流程 ----------------

    def run(self):
        report = ReconciliationReport()
        self._reconcile_orders(report)
        self._reconcile_positions(report)

        if report.critical:
            for discrepancy in report.critical:
                logger.critical(
                    "Reconciliation | %s | %s | %s",
                    discrepancy.kind, discrepancy.symbol or "-",
                    discrepancy.detail,
                )
        elif report.discrepancies:
            for discrepancy in report.discrepancies:
                logger.warning(
                    "Reconciliation | %s | %s | %s",
                    discrepancy.kind, discrepancy.symbol or "-",
                    discrepancy.detail,
                )

        return report

    # ---------------- 1. 狀態不明的訂單 ----------------

    def _reconcile_orders(self, report):
        try:
            orders = self.store.unresolved()
        except Exception as exc:
            logger.exception("Reconciliation | 讀不到待對帳訂單")
            report.errors.append(f"讀取訂單失敗:{type(exc).__name__}: {exc}")
            return

        report.checked_orders = len(orders)

        for order in orders:
            self._resolve_order(order, report)

    def _resolve_order(self, order, report):
        if self._fetch_order is None:
            report.discrepancies.append(Discrepancy(
                kind=ORDER_STILL_UNKNOWN,
                symbol=order.symbol,
                client_order_id=order.client_order_id,
                detail="沒有查詢來源,狀態仍然不明。這張單不可以重送。",
            ))
            return

        try:
            remote = self._fetch_order(order.client_order_id, order.symbol)
        except Exception as exc:
            logger.exception(
                "Reconciliation | 查詢訂單失敗 | %s", order.client_order_id,
            )
            report.discrepancies.append(Discrepancy(
                kind=ORDER_STILL_UNKNOWN,
                symbol=order.symbol,
                client_order_id=order.client_order_id,
                detail=f"查詢失敗:{type(exc).__name__}: {exc}",
            ))
            return

        if not remote:
            # 交易所沒有這張單 = 它從來沒被接受過。這是可以確定的答案。
            self._apply(order, OrderState.REJECTED, "對帳:交易所查無此單", report)
            return

        target = OrderState.parse(remote.get("state"), OrderState.UNKNOWN)

        if target is OrderState.UNKNOWN:
            report.discrepancies.append(Discrepancy(
                kind=ORDER_STILL_UNKNOWN,
                symbol=order.symbol,
                client_order_id=order.client_order_id,
                detail=f"交易所回報的狀態無法辨識:{remote.get('state')!r}",
                remote=dict(remote),
            ))
            return

        if remote.get("filled_quantity") is not None:
            order.filled_quantity = float(remote["filled_quantity"])
        if remote.get("average_price") is not None:
            order.average_fill_price = float(remote["average_price"])
        if remote.get("exchange_order_id"):
            order.exchange_order_id = remote["exchange_order_id"]

        self._apply(order, target, "對帳:以交易所狀態為準", report)

    def _apply(self, order, target, reason, report):
        """
        把交易所的事實寫回本地。

        狀態機不允許的轉移**不會硬寫** —— 那代表本地紀錄與交易所的說法
        在邏輯上兜不起來,那本身就是一個要人看的差異。
        """
        try:
            previous = order.state
            sm.transition(order, target, reason=reason)
        except sm.IllegalTransition as exc:
            report.discrepancies.append(Discrepancy(
                kind=ORDER_STILL_UNKNOWN,
                symbol=order.symbol,
                client_order_id=order.client_order_id,
                detail=f"本地狀態與交易所兜不起來:{exc}",
            ))
            return

        try:
            self.store.save(order)
            self.store.record_event(
                order.client_order_id, previous, order.state, reason,
            )
            self.store.mark_reconciled(order.client_order_id)
        except Exception as exc:
            report.errors.append(
                f"{order.client_order_id} 對帳結果寫入失敗:{exc}"
            )
            return

        report.resolved.append(order.client_order_id)
        logger.info(
            "Reconciliation | ORDER_RESOLVED | %s | %s -> %s",
            order.client_order_id, previous.value, order.state.value,
        )

    # ---------------- 2 & 3. 部位 ----------------

    def _reconcile_positions(self, report):
        try:
            local = {p.get("symbol"): p for p in self._local()}
        except Exception as exc:
            logger.exception("Reconciliation | 讀不到本地部位")
            report.errors.append(f"讀取本地部位失敗:{type(exc).__name__}: {exc}")
            return

        report.checked_positions = len(local)

        # 沒有停損的部位:不需要交易所也看得出來
        for symbol, position in local.items():
            if position.get("stoploss") is None:
                report.discrepancies.append(Discrepancy(
                    kind=UNPROTECTED_POSITION,
                    symbol=symbol,
                    detail="本地部位沒有停損,沒有虧損上限",
                    local=dict(position),
                ))

        if self._fetch_positions is None:
            # 沒有交易所來源時只做本地檢查。這不是錯誤,是模擬盤的常態。
            return

        try:
            remote_list = self._fetch_positions() or []
        except Exception as exc:
            logger.exception("Reconciliation | 讀不到交易所部位")
            report.errors.append(f"讀取交易所部位失敗:{type(exc).__name__}: {exc}")
            return

        remote = {p.get("symbol"): p for p in remote_list}

        for symbol, position in local.items():
            if symbol not in remote:
                report.discrepancies.append(Discrepancy(
                    kind=LOCAL_ONLY_POSITION,
                    symbol=symbol,
                    detail="本地有這個部位,交易所沒有。本地紀錄可能沒跟上平倉。",
                    local=dict(position),
                ))
                continue

            self._compare_size(symbol, position, remote[symbol], report)

        for symbol, position in remote.items():
            if symbol in local:
                continue

            # 最危險的一種:沒有人在管的倉位。
            # 沒有停損、不會被監控、也不在風控的計算裡。
            report.discrepancies.append(Discrepancy(
                kind=EXCHANGE_ONLY_POSITION,
                symbol=symbol,
                detail="交易所有這個部位,本地完全不知道。這個倉位沒有人在管。",
                remote=dict(position),
            ))

    def _compare_size(self, symbol, local, remote, report):
        local_qty = local.get("quantity")
        remote_qty = remote.get("quantity")

        if local_qty is None or remote_qty is None:
            return

        local_qty, remote_qty = float(local_qty), float(remote_qty)
        if local_qty == 0:
            return

        drift = abs(local_qty - remote_qty) / abs(local_qty)
        if drift <= SIZE_TOLERANCE:
            return

        report.discrepancies.append(Discrepancy(
            kind=SIZE_MISMATCH,
            symbol=symbol,
            detail=f"數量不一致:本地 {local_qty} vs 交易所 {remote_qty}"
                   f"(差異 {drift * 100:.2f}%)",
            local=dict(local),
            remote=dict(remote),
        ))


def build_default_reconciler(broker=None):
    """
    預設接到目前的 broker。

    模擬盤的 fetch_positions 是拿交易表跟自己比,發現不了部位漂移 ——
    那不是缺陷,是模擬盤的本質。真正的部位對帳只有實盤才有意義。
    這裡接上它是為了讓流程本身在模擬盤也跑得通。
    """
    if broker is None:
        from agmcis.execution.engine import get_engine
        broker = get_engine().broker

    return Reconciler(
        fetch_order=broker.fetch_order,
        fetch_positions=broker.fetch_positions,
    )


def run_reconciliation(reconciler=None, **kwargs):
    """排程器的入口。"""
    if reconciler is None:
        reconciler = Reconciler(**kwargs) if kwargs else build_default_reconciler()
    return reconciler.run().to_dict()
