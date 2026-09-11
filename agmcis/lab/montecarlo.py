"""
Monte Carlo 重抽樣。

一次回測只給你**一條**權益曲線。那條曲線裡有多少是優勢、多少是運氣,
單看它本身答不出來 —— 尤其是交易筆數不多的時候。

做法:把實際發生的交易結果當成一個分佈,重複抽樣幾千次,
看「同樣一組交易、不同的發生順序或不同的組合」會長成什麼樣子。

用 **R 倍數**(單筆損益 ÷ 單筆風險)而不是 USDT 金額重抽樣,
因為 R 倍數與部位大小無關,可以乾淨地套用固定比例風險模型。

兩個必須講清楚的前提(否則這些數字會被過度解讀):

  1. **假設每筆交易彼此獨立。** 實際上連虧常常成群出現(同一種市況),
     所以真實的最大回撤通常比這裡算出來的更糟,不是更好。
  2. **只能重抽已經發生過的交易。** 歷史上沒出現過的極端行情,
     Monte Carlo 不會憑空生出來。
"""
import math
import random
from dataclasses import dataclass, field
from typing import List, Optional

DEFAULT_RUNS = 2000
DEFAULT_RISK_PCT = 1.0

# 權益跌到起始資金的這個比例就算「破產」
DEFAULT_RUIN_THRESHOLD = 0.5


@dataclass
class MonteCarloResult:
    runs: int = 0
    trades_per_run: int = 0
    mode: str = "bootstrap"

    median_return_pct: float = 0.0
    mean_return_pct: float = 0.0
    p05_return_pct: float = 0.0
    p95_return_pct: float = 0.0
    worst_return_pct: float = 0.0
    best_return_pct: float = 0.0

    median_max_drawdown_pct: float = 0.0
    p95_max_drawdown_pct: float = 0.0
    worst_max_drawdown_pct: float = 0.0

    probability_of_profit: float = 0.0
    risk_of_ruin: float = 0.0

    warnings: List[str] = field(default_factory=list)

    def to_dict(self):
        return dict(self.__dict__)

    def summary_lines(self):
        return [
            f"模擬次數     {self.runs}  (每次 {self.trades_per_run} 筆,{self.mode})",
            f"報酬中位數   {self.median_return_pct:+.2f}%"
            f"   5% 分位 {self.p05_return_pct:+.2f}%"
            f"   95% 分位 {self.p95_return_pct:+.2f}%",
            f"最大回撤     中位數 {self.median_max_drawdown_pct:.2f}%"
            f"   95% 分位 {self.p95_max_drawdown_pct:.2f}%"
            f"   最差 {self.worst_max_drawdown_pct:.2f}%",
            f"獲利機率     {self.probability_of_profit * 100:.1f}%",
            f"破產機率     {self.risk_of_ruin * 100:.1f}%",
        ]


def r_multiples(result):
    """從回測結果取出每筆交易的 R 倍數。取不到 R 的交易會被略過。"""
    values = []
    for trade in result.closed_trades:
        r = trade.r_multiple
        if r is not None:
            values.append(float(r))
    return values


def _percentile(sorted_values, fraction):
    if not sorted_values:
        return 0.0
    position = fraction * (len(sorted_values) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return sorted_values[int(position)]
    weight = position - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def _simulate_one(sequence, risk_pct, ruin_threshold):
    """跑一條權益曲線,回傳 (報酬率%, 最大回撤%, 是否破產)。"""
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    ruined = False

    risk_fraction = risk_pct / 100.0

    for r in sequence:
        equity *= (1 + risk_fraction * r)

        # 權益歸零或變負在現實中不會發生(會先被強平),夾在 0
        if equity <= 0:
            equity = 0.0
            ruined = True

        if equity > peak:
            peak = equity

        drawdown = (peak - equity) / peak if peak > 0 else 0.0
        max_drawdown = max(max_drawdown, drawdown)

        if equity <= ruin_threshold:
            ruined = True

        if equity <= 0:
            break

    return (equity - 1) * 100, max_drawdown * 100, ruined


def run(result, runs=DEFAULT_RUNS, risk_pct=DEFAULT_RISK_PCT,
        mode="bootstrap", ruin_threshold=DEFAULT_RUIN_THRESHOLD, seed=None):
    """
    mode="bootstrap":有放回抽樣。檢驗「這組交易的分佈」能產生什麼結果。
    mode="shuffle"  :無放回重排。只檢驗順序的影響,每次用到的交易完全相同。
    """
    if mode not in ("bootstrap", "shuffle"):
        raise ValueError(f"未知的 mode:{mode}")

    values = r_multiples(result)
    output = MonteCarloResult(runs=runs, trades_per_run=len(values), mode=mode)

    if not values:
        output.runs = 0
        output.warnings.append("沒有可用的交易(或都算不出 R 倍數),無法模擬。")
        return output

    if len(values) < 30:
        output.warnings.append(
            f"只有 {len(values)} 筆交易。重抽樣不會創造出資訊 —— "
            f"樣本太小時模擬結果同樣不可信。"
        )

    rng = random.Random(seed)

    returns = []
    drawdowns = []
    ruins = 0

    for _ in range(runs):
        if mode == "bootstrap":
            sequence = [values[rng.randrange(len(values))] for _ in values]
        else:
            sequence = values[:]
            rng.shuffle(sequence)

        ret, drawdown, ruined = _simulate_one(sequence, risk_pct, ruin_threshold)
        returns.append(ret)
        drawdowns.append(drawdown)
        ruins += 1 if ruined else 0

    returns.sort()
    drawdowns.sort()

    output.median_return_pct = round(_percentile(returns, 0.5), 4)
    output.mean_return_pct = round(sum(returns) / len(returns), 4)
    output.p05_return_pct = round(_percentile(returns, 0.05), 4)
    output.p95_return_pct = round(_percentile(returns, 0.95), 4)
    output.worst_return_pct = round(returns[0], 4)
    output.best_return_pct = round(returns[-1], 4)

    output.median_max_drawdown_pct = round(_percentile(drawdowns, 0.5), 4)
    output.p95_max_drawdown_pct = round(_percentile(drawdowns, 0.95), 4)
    output.worst_max_drawdown_pct = round(drawdowns[-1], 4)

    output.probability_of_profit = round(
        len([r for r in returns if r > 0]) / len(returns), 4,
    )
    output.risk_of_ruin = round(ruins / runs, 4)

    if output.risk_of_ruin > 0.05:
        output.warnings.append(
            f"破產機率 {output.risk_of_ruin * 100:.1f}% —— "
            f"以 {risk_pct}% 單筆風險運行這個策略太危險。"
        )

    if output.p05_return_pct < 0 < output.median_return_pct:
        output.warnings.append(
            "5% 分位是虧損。中位數好看不代表不會遇到那條差的路徑。"
        )

    return output
