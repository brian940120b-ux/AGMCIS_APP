"""
新系統 — 依回撤契約決定部位規模 · 2026-09-08

執政官的畢業契約第四條:最大回撤 <= 15%。我不改那個數字。
既然策略的風險是線性可調的(不加槓桿、只按比例縮小),
那麼「達不到契約」與「不能交易」之間還有一步:**持有少一點**。

紀律:縮放倍數只用**訓練段**的實測回撤算,驗證段不得參與。
用驗證段調規模就是用答案調參數。
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from portfolio.judge import MAX_DD_CONTRACT_PCT


def scale_for_contract(train_dd_pct: float) -> float:
    """回傳達成回撤契約所需的部位倍數(上限 1.0,不加槓桿)。"""
    if train_dd_pct <= 0:
        return 1.0
    return min(1.0, MAX_DD_CONTRACT_PCT / train_dd_pct)
