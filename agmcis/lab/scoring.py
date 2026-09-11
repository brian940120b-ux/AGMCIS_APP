"""
最佳化目標與 Strategy Health Score。

Master Prompt 第二節寫得很清楚:真正的最佳化目標不是報酬率,而是
**Risk Adjusted Return + Positive Expectancy + Robustness + Low Drawdown + Consistency**。

這個模組存在的理由,就是讓「用總報酬率挑參數」這件事變得不方便。
用總報酬率挑出來的參數幾乎必然是過擬合的:報酬率對少數幾筆極端交易
極度敏感,而那幾筆通常不會重演。
"""
from dataclasses import dataclass, field
from typing import List, Optional

# 樣本數門檻。低於這個數字的結果不足以下結論。
MIN_TRADES_FOR_CONFIDENCE = 30

# OOS 相對 IS 退化超過這個比例就視為過擬合訊號
DEGRADATION_WARNING = 0.5
DEGRADATION_SEVERE = 0.8


def objective(metrics):
    """
    挑參數用的分數。**不是**總報酬率。

    以每筆交易的期望值(R 倍數)為主體,再用回撤與樣本數打折:

        score = expectancy_r × 樣本數信心 ÷ (1 + 最大回撤比例)

    期望值 <= 0 時直接回傳負分 —— 不管報酬率多好看,
    負期望值的策略長期一定虧。

    回傳 None 代表這組結果根本不夠格被比較(沒有交易)。
    """
    if metrics is None or metrics.total_trades == 0:
        return None

    expectancy = metrics.expectancy_r
    if expectancy is None:
        return None

    if expectancy <= 0:
        return expectancy      # 負的就是負的,不再加權

    # 樣本數信心:30 筆給滿分,越少折越多。開根號避免「筆數多就贏」。
    confidence = min(1.0, (metrics.total_trades / MIN_TRADES_FOR_CONFIDENCE) ** 0.5)

    drawdown_penalty = 1 + max(0.0, metrics.max_drawdown_pct) / 100.0

    return expectancy * confidence / drawdown_penalty


@dataclass
class HealthScore:
    """
    策略健康度。0-100,但**分數高不等於可以上線**。

    這是一組「能不能拒絕」的判準,不是排行榜。
    任何一項 blocker 成立,不管總分多少都是 REJECT。
    """
    score: float = 0.0
    raw_score: float = 0.0
    verdict: str = "REJECT"
    components: dict = field(default_factory=dict)
    blockers: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self):
        return dict(self.__dict__)

    def summary_lines(self):
        lines = [f"Strategy Health  {self.score:.1f}/100   判定 {self.verdict}"]
        if self.blockers and self.raw_score != self.score:
            lines.append(
                f"  (未計 blocker 前的分項合計 {self.raw_score:.1f} —— "
                f"blocker 成立時分數一律歸零,避免被當成「其實還不錯」)"
            )
        for name, value in self.components.items():
            lines.append(f"  {name:<16} {value:.1f}")
        for blocker in self.blockers:
            lines.append(f"  ⛔ {blocker}")
        for warning in self.warnings:
            lines.append(f"  ⚠️  {warning}")
        return lines


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def health_score(oos_metrics, is_metrics=None, monte_carlo=None):
    """
    以 **OOS 的表現**為主體評分。IS 的表現只用來偵測退化,不加分 ——
    在調參用過的資料上表現好本來就是應該的,那不是證據。
    """
    health = HealthScore()

    if oos_metrics is None or oos_metrics.total_trades == 0:
        health.blockers.append("OOS 沒有任何交易,無從判斷。")
        return health

    components = {}

    # --- 期望值(最重要)---
    expectancy = oos_metrics.expectancy_r
    if expectancy is None:
        health.blockers.append("算不出 OOS 期望值。")
        return health

    if expectancy <= 0:
        health.blockers.append(
            f"OOS 期望值 {expectancy:+.3f} R 不為正。長期必定虧損。"
        )
    components["期望值"] = _clamp(expectancy / 0.3) * 35

    # --- Profit Factor ---
    profit_factor = oos_metrics.profit_factor
    if profit_factor is None:
        # 沒有虧損單 -> PF 無法評估,不是無限大
        health.warnings.append("OOS 沒有虧損單,Profit Factor 無法評估。")
        components["ProfitFactor"] = 0.0
    else:
        if profit_factor < 1.0:
            health.blockers.append(f"OOS Profit Factor {profit_factor:.2f} < 1。")
        components["ProfitFactor"] = _clamp((profit_factor - 1.0) / 0.8) * 20

    # --- 回撤 ---
    drawdown = oos_metrics.max_drawdown_pct
    components["回撤"] = _clamp(1 - drawdown / 40.0) * 15
    if drawdown > 40:
        health.blockers.append(f"OOS 最大回撤 {drawdown:.1f}% 過大。")

    # --- 樣本數 ---
    trades = oos_metrics.total_trades
    components["樣本數"] = _clamp(trades / MIN_TRADES_FOR_CONFIDENCE) * 15
    if trades < MIN_TRADES_FOR_CONFIDENCE:
        health.blockers.append(
            f"OOS 只有 {trades} 筆交易,少於 {MIN_TRADES_FOR_CONFIDENCE} 筆,"
            f"無論數字多好看都不足以下結論。"
        )

    # --- IS -> OOS 退化 ---
    degradation = None
    if is_metrics is not None and is_metrics.expectancy_r:
        degradation = expectancy_degradation(is_metrics, oos_metrics)

    if degradation is None:
        components["穩健度"] = 0.0
        health.warnings.append("沒有 IS 對照,無法評估退化程度。")
    else:
        components["穩健度"] = _clamp(1 - degradation) * 15
        if degradation >= DEGRADATION_SEVERE:
            health.blockers.append(
                f"OOS 期望值比 IS 退化 {degradation * 100:.0f}%,典型的過擬合。"
            )
        elif degradation >= DEGRADATION_WARNING:
            health.warnings.append(
                f"OOS 期望值比 IS 退化 {degradation * 100:.0f}%,偏高。"
            )

    # --- 強平 ---
    if oos_metrics.liquidations:
        health.blockers.append(
            f"OOS 出現 {oos_metrics.liquidations} 次強制平倉。"
        )

    # --- Monte Carlo ---
    if monte_carlo is not None:
        if monte_carlo.risk_of_ruin > 0.05:
            health.blockers.append(
                f"Monte Carlo 破產機率 {monte_carlo.risk_of_ruin * 100:.1f}%。"
            )
        if monte_carlo.p05_return_pct < 0:
            health.warnings.append(
                f"Monte Carlo 5% 分位為 {monte_carlo.p05_return_pct:+.1f}%,"
                f"有相當機率走到虧損路徑。"
            )

    health.components = {k: round(v, 2) for k, v in components.items()}
    health.raw_score = round(sum(components.values()), 2)
    health.score = health.raw_score

    if health.blockers:
        # blocker 是絕對的。分數再高也不能上線,所以分數就歸零 ——
        # 留著「REJECT 但 85 分」這種組合,看報告的人遲早會去凹那個 85。
        health.score = 0.0
        health.verdict = "REJECT"
    elif health.score >= 70:
        health.verdict = "PASS"
    else:
        health.verdict = "MARGINAL"

    return health


def expectancy_degradation(is_metrics, oos_metrics):
    """
    OOS 期望值相對 IS 退化多少(0 = 沒退化,1 = 全部消失,>1 = 變負)。

    IS 期望值不為正時回 None —— 連調參用的資料上都沒優勢,
    談退化沒有意義。
    """
    if is_metrics is None or oos_metrics is None:
        return None

    is_expectancy = is_metrics.expectancy_r
    oos_expectancy = oos_metrics.expectancy_r

    if is_expectancy is None or oos_expectancy is None or is_expectancy <= 0:
        return None

    return (is_expectancy - oos_expectancy) / is_expectancy
