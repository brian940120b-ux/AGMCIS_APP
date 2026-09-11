"""
AGMCIS — AI Private Trading System。

這個套件是 Phase 1 開始建立的新分層。策略是「原地改造」:
新的型別與組態放進 agmcis/,根目錄的舊模組逐步變成 re-export shim,
生產服務的 import 路徑完全不用改,可以一個 Phase 一個 Phase 搬。

分層(依 Phase 逐步補齊):
    agmcis/core/        型別、資料結構、例外        ← Phase 1
    agmcis/config/      組態                        ← Phase 1
    agmcis/scheduling/  排程                        ← Phase 1
    agmcis/data/        市場資料與資料品質          ← Phase 2
    agmcis/exchange/    ExchangeAdapter + BingX     ← Phase 2/3
    agmcis/risk/        Risk Engine + Position Sizing ← Phase 5
    agmcis/strategy/    策略註冊表                  ← Phase 6
    agmcis/backtest/    回測                        ← Phase 7
    agmcis/agents/      12 個 Agent                 ← Phase 9
    agmcis/execution/   Execution Engine            ← Phase 12
"""

__version__ = "1.1.0"
