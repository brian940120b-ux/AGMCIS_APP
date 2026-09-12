"""
對帳(Master Prompt 第八節的 reconciler.py)。

## 這裡沒有重複實作

對帳在 `agmcis/execution/reconciliation.py`,與 executor 同樣的理由:
它不是交易所專屬的。

那個模組最重要的性質:**對帳只更正本地紀錄,絕不下單。**
一個會自己「修正」差異的對帳程式,在交易所回報錯誤時會製造
真實的部位。差異一律回報給人處理。
"""
from agmcis.execution.reconciliation import (  # noqa: F401
    Discrepancy,
    ReconciliationReport,
    Reconciler,
    run_reconciliation,
)

__all__ = [
    "Reconciler", "ReconciliationReport", "Discrepancy", "run_reconciliation",
]
