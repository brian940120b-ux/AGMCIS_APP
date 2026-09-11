"""
相容層。真正的實作在 agmcis/backtest/。

Phase 7 之前這個檔案自己跑回測:資料抓 Binance、訊號與成交在同一根 K 棒、
沒有停損、沒有成本、每筆押上 100% 資金。那套數字不能用來決定上線與否。

現在 load_data() 與 run_strategy() 都轉接到新引擎。
(Phase 8 之後 strategy_lab.py 與 strategy_optimizer.py 改走 agmcis.lab,
不再用這裡的 run_strategy;這個 shim 保留給其他仍用舊介面的呼叫端。)
"""
from agmcis.backtest.legacy import (  # noqa: F401
    add_indicators,
    build_signal_fns,
    load_data,
    run_strategy,
    run_strategy_detailed,
)

__all__ = [
    "add_indicators",
    "build_signal_fns",
    "load_data",
    "run_strategy",
    "run_strategy_detailed",
]
