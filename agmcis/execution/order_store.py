"""
訂單持久化。

Phase 12 的 Order 只活在記憶體裡。程式重啟時 `UNKNOWN` 狀態的訂單就消失了 ——
而那正是**最需要被記住**的狀態:系統不知道交易所收到了什麼。
忘記它等於放棄對帳,而放棄對帳之後,任何重試都可能開出重複倉位。

兩張表:

    orders          每張單的當前狀態
    order_events    每一次狀態轉移的稽核軌跡

為什麼要事件表:出事之後要回答「那張單當時到底走到哪一步」,
只有最終狀態是不夠的。一張最後變成 `CLOSED` 的單,
是「正常成交後平倉」還是「裸倉被緊急平掉」,差別很大。
"""
import logging
from typing import List, Optional

from agmcis.core.enums import MarketType, OrderSide, OrderState, OrderType
from agmcis.core.models import Order

logger = logging.getLogger("agmcis.execution.order_store")

ORDER_COLUMNS = """
    client_order_id,
    exchange_order_id,
    symbol,
    market_type,
    side,
    order_type,
    state,
    quantity,
    filled_quantity,
    price,
    average_fill_price,
    reduce_only,
    reject_reason,
    trade_id,
    source
"""

# ⚠️ 這份清單與 _row_to_order() 的索引綁死。加欄位時兩邊都要改。
# Phase 0.5 與 Phase 10 各踩過一次同類的錯。


def _f(value):
    return float(value) if value is not None else None


def _row_to_order(row):
    order = Order(
        client_order_id=row[0],
        symbol=row[2],
        market_type=MarketType.parse(row[3], MarketType.PERPETUAL),
        side=OrderSide.parse(row[4], OrderSide.BUY),
        order_type=OrderType.parse(row[5], OrderType.MARKET),
        quantity=_f(row[7]) or 0.0,
        state=OrderState.parse(row[6], OrderState.UNKNOWN),
    )
    order.exchange_order_id = row[1]
    order.filled_quantity = _f(row[8]) or 0.0
    order.price = _f(row[9])
    order.average_fill_price = _f(row[10])
    order.reduce_only = bool(row[11])
    order.reject_reason = row[12]
    return order


class OrderStore:
    """
    資料庫存取集中在這裡,讓 Execution Engine 不必知道 SQL。

    `transaction` 可注入,測試就不需要真的資料庫。
    """

    def __init__(self, transaction=None):
        self._transaction = transaction

    @property
    def transaction(self):
        if self._transaction is None:
            from db import transaction
            self._transaction = transaction
        return self._transaction

    # ---------------- 寫入 ----------------

    def save(self, order, trade_id=None, source="EXEC"):
        """
        建立或更新一張單。以 client_order_id 為唯一鍵 upsert。

        client_order_id 是我們自己產生的,所以同一張單重複儲存是安全的 ——
        但**不會**因此新增第二列。重複列會讓對帳看到兩張單,
        而那正是對帳要偵測的問題本身。
        """
        with self.transaction() as cur:
            cur.execute(
                f"""
                INSERT INTO orders ({ORDER_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (client_order_id) DO UPDATE SET
                    exchange_order_id  = EXCLUDED.exchange_order_id,
                    state              = EXCLUDED.state,
                    filled_quantity    = EXCLUDED.filled_quantity,
                    average_fill_price = EXCLUDED.average_fill_price,
                    reject_reason      = EXCLUDED.reject_reason,
                    trade_id           = COALESCE(EXCLUDED.trade_id, orders.trade_id),
                    updated_at         = CURRENT_TIMESTAMP
                RETURNING id;
                """,
                (
                    order.client_order_id, order.exchange_order_id, order.symbol,
                    order.market_type.value, order.side.value,
                    order.order_type.value, order.state.value,
                    order.quantity, order.filled_quantity, order.price,
                    order.average_fill_price, order.reduce_only,
                    order.reject_reason, trade_id, source,
                ),
            )
            return cur.fetchone()[0]

    def record_event(self, client_order_id, from_state, to_state, reason=None):
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO order_events
                    (client_order_id, from_state, to_state, reason)
                VALUES (%s, %s, %s, %s);
                """,
                (
                    client_order_id,
                    from_state.value if hasattr(from_state, "value") else from_state,
                    to_state.value if hasattr(to_state, "value") else to_state,
                    reason,
                ),
            )

    def mark_reconciled(self, client_order_id):
        with self.transaction() as cur:
            cur.execute(
                "UPDATE orders SET reconciled_at = CURRENT_TIMESTAMP "
                "WHERE client_order_id = %s;",
                (client_order_id,),
            )

    # ---------------- 讀取 ----------------

    def get(self, client_order_id) -> Optional[Order]:
        with self.transaction() as cur:
            cur.execute(
                f"SELECT {ORDER_COLUMNS} FROM orders WHERE client_order_id = %s;",
                (client_order_id,),
            )
            row = cur.fetchone()
        return _row_to_order(row) if row else None

    def by_states(self, states) -> List[Order]:
        values = [s.value if hasattr(s, "value") else s for s in states]
        with self.transaction() as cur:
            cur.execute(
                f"SELECT {ORDER_COLUMNS} FROM orders "
                f"WHERE state = ANY(%s) ORDER BY id ASC;",
                (values,),
            )
            rows = cur.fetchall()
        return [_row_to_order(row) for row in rows]

    def unresolved(self) -> List[Order]:
        """
        狀態不明、需要對帳的訂單。

        這是整個對帳流程的入口 —— 系統不知道交易所收到了什麼的那些單。
        """
        return self.by_states([
            OrderState.UNKNOWN, OrderState.TIMEOUT, OrderState.SUBMITTING,
        ])

    def open_orders(self) -> List[Order]:
        """還沒結束、也還沒平倉的訂單。"""
        return self.by_states([
            OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED,
            OrderState.FILLED, OrderState.PROTECTED,
        ])

    def events(self, client_order_id):
        with self.transaction() as cur:
            cur.execute(
                "SELECT from_state, to_state, reason, created_at "
                "FROM order_events WHERE client_order_id = %s ORDER BY id ASC;",
                (client_order_id,),
            )
            rows = cur.fetchall()

        return [
            {"from": row[0], "to": row[1], "reason": row[2], "at": row[3]}
            for row in rows
        ]


_STORE = None


def get_store():
    global _STORE
    if _STORE is None:
        _STORE = OrderStore()
    return _STORE


def set_store(store):
    global _STORE
    _STORE = store
