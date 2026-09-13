"""
新系統 — 成本模型 · 2026-09-08

═══ 為什麼成本要獨立一支,而且是第一支 ═══
舊系統十一次「兩把尺」裡有四次是成本:影子不收資金費、滑點差 9.5 倍、
摩擦沒下限、法庭與影子各寫各的常數。每一次都是把負的算成正的。

所以新系統從第一天就一條規則:**成本只有一個來源,誰都不准自己寫常數。**

═══ 持有永續 ≠ 持有現貨(舊系統從來沒算過這件事)═══
基準線是「買入持有」,那是現貨式的 —— 買了就放著,不再付錢。
但這個帳戶只能用永續合約,而永續要貼住現貨,**多方每 8 小時付費率**。
實測(測量官 45,895 筆快照 × 62 幣):背景費率中位 0.0065%/8h
= **年化 7.12%**,而且當下 10/10 個幣都是正費率。

也就是說:用永續複製「買入持有」,每年要先輸給現貨 7% 才打平。
不把這一項算進去,任何「打敗買入持有」的結論都是假的。
舊系統的基準對照(-35.95pp)沒有這個問題,因為它根本沒持有;
新系統以持有為主,這一項就變成最大的單一成本。
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.logging import get_logger

log = get_logger("portfolio.costs")

BASE = Path(__file__).resolve().parents[1]
GAUGE_HIST = BASE / "data" / "gauge_history.jsonl"

# ── 成本常數:全部來自實測,不是假設 ─────────────────────────
# 手續費與滑點共用法庭的 friction_tier(),不在此重寫。
# 這裡只放法庭沒有的那一項:持有永續的資金費率。
FUNDING_PERIODS_PER_YEAR = 3 * 365      # 每 8 小時一次
FUNDING_FALLBACK_8H_PCT = 0.0065        # 實測背景中位數(2026-07-16 起 53 天)

# ── 手續費與滑點:2026-09-06 實測 BingX,不是假設 ─────────────
#   費率        maker 0.02% · taker 0.05%(合約端點直接回報)
#   價差中位數  0.0068% → 半價差 0.0034%
#   市價衝擊    10,000 USDT:BTC 0.0000% / ETH 0.0005% / ENA 0.0072%
#   簿子深度    570 萬 ~ 6,300 萬 USDT —— 我們的單佔簿子 0.02%
# 滑點下限取 0.02%,已涵蓋半價差與衝擊。
#
# 舊系統用的「悲觀檔」滑點下限 0.19%,比實測高 38 倍 ——
# 那不是保守,是在量另一個遊戲:在 38 倍成本的世界裡,
# 任何合理的優勢都會被判死。這裡只留實測值,不留假設值。
TAKER_FEE_PCT = 0.05
SLIP_FLOOR_PCT = 0.02
# ─────────────────────────────────────────────────────────


def round_trip_pct() -> float:
    """一次進出的手續費 + 滑點。唯一來源,誰都不准自己寫常數。"""
    return TAKER_FEE_PCT * 2 + SLIP_FLOOR_PCT * 2


def measured_funding_8h_pct(symbol: str | None = None) -> float:
    """實測資金費率中位數(%/8h)。symbol 為 None 時取全市場。

    正值 = 做多方付錢。回傳一律取正負號的原值,
    由呼叫端決定方向(做多付、做空收)。
    """
    vals: list[float] = []
    try:
        with GAUGE_HIST.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    snap = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for r in snap.get("rows") or []:
                    if symbol and r.get("symbol") != symbol:
                        continue
                    f = r.get("funding_pct_8h")
                    if f is not None:
                        vals.append(float(f))
    except OSError as e:
        log.warning(f"費率歷史讀取失敗,退回實測預設:{e}")
    if not vals:
        return FUNDING_FALLBACK_8H_PCT
    return statistics.median(vals)


def funding_annual_pct(symbol: str | None = None) -> float:
    """持有一年的資金費成本(%)。正值 = 做多要付這麼多。"""
    return measured_funding_8h_pct(symbol) * FUNDING_PERIODS_PER_YEAR


def holding_cost_pct(days: float, symbol: str | None = None,
                     direction: str = "LONG") -> float:
    """持有 N 天的資金費成本(%)。做空為負(收錢)。

    刻意不做「未來費率會怎樣」的假設 —— 只用實測中位數外推,
    並且在報告裡一律標明這是外推而非預測。
    """
    per_day = measured_funding_8h_pct(symbol) * 3
    sign = 1.0 if direction.upper() == "LONG" else -1.0
    return per_day * days * sign
