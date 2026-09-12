"""
成本敏感度(Master Prompt 第三十七節的 Slippage Variation / Fee Variation)。

Monte Carlo 重抽的是**已經發生的交易結果**。它回答「同樣一組交易、
不同的順序會長成什麼樣」,但它答不出「如果滑點比預期高一倍會怎樣」——
因為成本變高不只是把每筆損益減掉一點,它會改變:

  * 哪些交易還有得賺(小賺的那些會變成小虧)
  * 強平價的位置(成本吃掉保證金)
  * 進場價與出場價(滑點直接改變成交價)

所以成本敏感度**必須重跑回測**,不能在結果上做加減。在損益上直接扣
一個估計的成本,會系統性低估影響 —— 而低估的方向正是危險的那一邊。

## 這個模組回答的問題

「成本要漲到幾倍,這個策略的優勢才會消失?」

那個數字叫 break-even multiple,是這裡最有價值的輸出。它把
「手續費 0.05%」這種抽象的數字變成一句可以判斷的話:

    優勢在成本 1.4 倍時消失  ->  很脆弱。實際滑點只要比估計差四成就沒了。
    優勢在成本 6 倍時才消失  ->  相當穩健。

## 一個誠實的限制

這裡放大的是**估計的成本模型**,而那個模型本身可能是錯的。
如果 CostModel 的預設值離 BingX 實際費率很遠,1.0 倍就已經不對了,
放大 3 倍也只是把一個錯的數字乘以三。

所以這個模組的輸出只能用來比較「策略對成本的敏感度」,
不能用來宣稱「成本 2 倍時實際會賺多少」。要後者必須先用
scripts/verify_bingx.py 把真實費率抓下來。
"""
import copy
import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger("agmcis.lab.cost_sensitivity")

# 預設要試哪幾倍。1.0 一定要在裡面 —— 沒有基準線就無法判斷退化幅度。
DEFAULT_MULTIPLIERS = (1.0, 1.5, 2.0, 3.0, 5.0)

# 這幾個欄位會被放大。maintenance / liquidation_fee 不放大 ——
# 它們是交易所規則,不是我們的估計誤差。
SCALED_FIELDS = ("maker_fee", "taker_fee", "slippage_pct", "spread_pct")

# funding 單獨放大:它的不確定性來源不同(市場情緒),
# 而且做空時是收而不是付,放大它的方向不一定是變差。
FUNDING_FIELD = "funding_rate_8h"


def scaled_costs(base, multiplier, scale_funding=True):
    """
    把成本模型放大 N 倍。回傳新的物件,**不改原本那個**。

    原地修改會讓同一個 CostModel 在多次呼叫之間累積放大,
    而那種錯誤在結果裡看起來只是「敏感度比想像中高」。
    """
    model = copy.copy(base)
    multiplier = float(multiplier)

    for field_name in SCALED_FIELDS:
        current = getattr(model, field_name, None)
        if current is not None:
            setattr(model, field_name, current * multiplier)

    if scale_funding:
        current = getattr(model, FUNDING_FIELD, None)
        if current is not None:
            setattr(model, FUNDING_FIELD, current * multiplier)

    return model


@dataclass
class CostPoint:
    multiplier: float = 1.0
    trades: int = 0
    total_return_pct: float = 0.0
    profit_factor: Optional[float] = None
    expectancy_usdt: float = 0.0
    win_rate: float = 0.0
    max_drawdown_pct: float = 0.0
    cost_drag_pct: float = 0.0
    liquidations: int = 0

    @property
    def has_edge(self):
        """
        還有優勢的定義:期望值為正**而且** Profit Factor 大於 1。

        只看期望值不夠 —— 一筆巨大的獲利可以讓一堆小虧的策略期望值為正,
        而那種策略在實際交易中不可用。
        """
        return self.expectancy_usdt > 0 and (self.profit_factor or 0) > 1.0

    def to_dict(self):
        return {
            "multiplier": self.multiplier,
            "trades": self.trades,
            "total_return_pct": round(self.total_return_pct, 4),
            "profit_factor": (
                round(self.profit_factor, 3)
                if self.profit_factor is not None else None
            ),
            "expectancy_usdt": round(self.expectancy_usdt, 4),
            "win_rate": round(self.win_rate, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "cost_drag_pct": round(self.cost_drag_pct, 4),
            "liquidations": self.liquidations,
            "has_edge": self.has_edge,
        }


@dataclass
class CostSensitivity:
    points: List[CostPoint] = field(default_factory=list)
    break_even_multiplier: Optional[float] = None
    verdict: str = "UNKNOWN"
    warnings: List[str] = field(default_factory=list)

    def to_dict(self):
        return {
            "points": [p.to_dict() for p in self.points],
            "break_even_multiplier": self.break_even_multiplier,
            "verdict": self.verdict,
            "warnings": list(self.warnings),
        }

    def summary_lines(self):
        lines = ["成本敏感度(倍數 / 報酬 / PF / 期望值 / 還有優勢)"]
        for point in self.points:
            pf = (
                f"{point.profit_factor:.2f}"
                if point.profit_factor is not None else "—"
            )
            lines.append(
                f"  {point.multiplier:>4.1f}x  "
                f"{point.total_return_pct:+8.2f}%  "
                f"PF {pf:>5}  "
                f"期望值 {point.expectancy_usdt:+8.2f}  "
                f"{'有' if point.has_edge else '沒有'}"
            )

        if self.break_even_multiplier is None:
            lines.append("優勢在測試範圍內沒有消失。")
        else:
            lines.append(
                f"優勢在成本約 {self.break_even_multiplier:.1f} 倍時消失。"
            )
        lines.append(f"判定:{self.verdict}")
        return lines


# 判定門檻。低於 1.5 代表實際滑點只要比估計差五成優勢就沒了。
FRAGILE_BELOW = 1.5
ROBUST_ABOVE = 3.0


def run(run_backtest, base_costs, multipliers=DEFAULT_MULTIPLIERS,
        scale_funding=True):
    """
    對每個倍數重跑一次回測。

    `run_backtest(costs) -> BacktestResult`,由呼叫端提供 —— 這個模組
    不知道怎麼組回測(要哪些 K 棒、哪個策略、什麼參數都是外面的事)。

    回傳 CostSensitivity。1.0 倍沒有優勢時直接說明,不去算 break-even:
    一個本來就沒有優勢的策略,「優勢在幾倍時消失」這個問題沒有意義。
    """
    from agmcis.backtest import metrics as metrics_module

    output = CostSensitivity()
    ordered = sorted(float(m) for m in multipliers)

    if not ordered or abs(ordered[0] - 1.0) > 1e-9:
        output.warnings.append(
            "倍數清單裡沒有 1.0。沒有基準線就無法判斷退化幅度。"
        )

    for multiplier in ordered:
        costs = scaled_costs(base_costs, multiplier, scale_funding=scale_funding)

        try:
            result = run_backtest(costs)
        except Exception as exc:
            logger.exception("成本敏感度:%sx 的回測失敗", multiplier)
            output.warnings.append(
                f"{multiplier}x 的回測失敗:{type(exc).__name__}: {exc}"
            )
            continue

        stats = metrics_module.compute(result)
        output.points.append(CostPoint(
            multiplier=multiplier,
            trades=stats.total_trades,
            total_return_pct=stats.total_return_pct,
            profit_factor=stats.profit_factor,
            expectancy_usdt=stats.expectancy_usdt,
            win_rate=stats.win_rate,
            max_drawdown_pct=stats.max_drawdown_pct,
            cost_drag_pct=stats.cost_drag_pct,
            liquidations=stats.liquidations,
        ))

    if not output.points:
        output.verdict = "NO_RESULT"
        output.warnings.append("沒有任何倍數跑出結果。")
        return output

    baseline = output.points[0]

    if not baseline.has_edge:
        output.verdict = "NO_EDGE_AT_BASELINE"
        output.warnings.append(
            "基準成本下就沒有優勢。「優勢在成本幾倍時消失」這個問題"
            "對這個策略沒有意義。"
        )
        return output

    for point in output.points:
        if not point.has_edge:
            output.break_even_multiplier = point.multiplier
            break

    if output.break_even_multiplier is None:
        output.verdict = "ROBUST"
        output.warnings.append(
            f"優勢在測試到的最高倍數({ordered[-1]}x)仍然存在。"
            f"這不代表它永遠存在 —— 只代表測試範圍不夠寬。"
        )
    elif output.break_even_multiplier <= FRAGILE_BELOW:
        output.verdict = "FRAGILE"
        output.warnings.append(
            f"優勢在成本 {output.break_even_multiplier}x 就消失。"
            f"實際滑點比估計差一點點就沒有優勢了 —— 這種策略不該上實單。"
        )
    elif output.break_even_multiplier >= ROBUST_ABOVE:
        output.verdict = "ROBUST"
    else:
        output.verdict = "MODERATE"

    return output
