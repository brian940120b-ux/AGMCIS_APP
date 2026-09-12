"""
執行(Master Prompt 第八節的 executor.py)。

## 這裡沒有重複實作 —— 而且它刻意不在 bingx 底下

Execution Engine 在 `agmcis/execution/engine.py`。它**不是交易所專屬的**:
訂單狀態機、開倉後強制停損保護、加倉、反手,這些邏輯與哪一家交易所
無關,換交易所時不該重寫。

而且順序很重要。執行層在 adapter **之上**,不是裡面:

    Risk Engine -> Trading Rules -> Execution Engine -> Adapter -> ccxt

把 executor 放進 bingx/ 會讓那個階層顛倒過來,而顛倒之後最容易
發生的事情是:某個地方繞過 Execution Engine 直接呼叫 adapter 下單,
於是那一筆沒有狀態機、沒有停損保護。
"""
from agmcis.execution.engine import ExecutionEngine, get_engine  # noqa: F401

__all__ = ["ExecutionEngine", "get_engine"]
