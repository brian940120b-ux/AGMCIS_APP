"""
回測績效指標(Master Prompt 第 35 條)。

舊回測只有:總報酬、勝率、最大回撤、Profit Factor。
缺 Expectancy、Sharpe、Sortino、Calmar、Recovery Factor、MFE/MAE、持倉時間。

其中 **Expectancy 比勝率重要得多**:
勝率 80% 但每次賺 1、虧 10 的策略是穩定虧錢的。
Master Prompt 第 2 條也明講真正的最佳化目標是
Risk Adjusted Return + Positive Expectancy,不是單純追求勝率。
"""
import math
from dataclasses import dataclass, field
from typing import List, Optional

# 一年的小時數,用來年化
HOURS_PER_YEAR = 24 * 365


@dataclass
class Metrics:
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0

    start_balance: float = 0.0
    end_balance: float = 0.0
    total_return_pct: float = 0.0
    annualised_return_pct: Optional[float] = None

    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: Optional[float] = None
    expectancy_usdt: float = 0.0
    expectancy_r: Optional[float] = None

    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    payoff_ratio: Optional[float] = None

    max_drawdown_pct: float = 0.0
    max_drawdown_usdt: float = 0.0
    sharpe: Optional[float] = None
    sortino: Optional[float] = None
    calmar: Optional[float] = None
    recovery_factor: Optional[float] = None

    avg_bars_held: float = 0.0
    avg_mfe_pct: float = 0.0
    avg_mae_pct: float = 0.0

    total_fees: float = 0.0
    total_funding: float = 0.0
    cost_drag_pct: float = 0.0

    exit_reasons: dict = field(default_factory=dict)
    long_trades: int = 0
    short_trades: int = 0
    liquidations: int = 0

    warnings: List[str] = field(default_factory=list)

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items()}

    def summary_lines(self):
        lines = [
            f"交易筆數     {self.total_trades}  (多 {self.long_trades} / 空 {self.short_trades})",
            f"勝率         {self.win_rate:.2f}%",
            f"總報酬       {self.total_return_pct:+.2f}%",
            f"Profit Factor {_fmt(self.profit_factor)}",
            f"Expectancy   {self.expectancy_usdt:+.4f} USDT / 筆"
            + (f"  ({self.expectancy_r:+.3f} R)" if self.expectancy_r is not None else ""),
            f"最大回撤     {self.max_drawdown_pct:.2f}%",
            f"Sharpe       {_fmt(self.sharpe)}   Sortino {_fmt(self.sortino)}",
            f"成本拖累     {self.cost_drag_pct:.2f}% of 起始資金"
            f"  (手續費 {self.total_fees:.2f} + Funding {self.total_funding:.2f})",
        ]
        if self.liquidations:
            lines.append(f"⚠️ 強制平倉   {self.liquidations} 次")
        return lines


def _fmt(value):
    return "n/a" if value is None else f"{value:.3f}"


def _drawdown(equity_curve):
    peak = equity_curve[0]
    max_dd_pct = 0.0
    max_dd_usdt = 0.0

    for value in equity_curve:
        peak = max(peak, value)
        if peak > 0:
            dd_pct = (peak - value) / peak * 100
            max_dd_pct = max(max_dd_pct, dd_pct)
        max_dd_usdt = max(max_dd_usdt, peak - value)

    return max_dd_pct, max_dd_usdt


def _stdev(values):
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def compute(result, candle_hours=1.0):
    """從 BacktestResult 算出完整指標。"""
    metrics = Metrics(
        start_balance=result.start_balance,
        end_balance=result.end_balance,
        warnings=list(result.warnings),
    )

    trades = result.closed_trades
    metrics.total_trades = len(trades)

    if result.start_balance > 0:
        metrics.total_return_pct = (
            (result.end_balance - result.start_balance) / result.start_balance * 100
        )

    curve = result.equity_curve or [result.start_balance]
    metrics.max_drawdown_pct, metrics.max_drawdown_usdt = _drawdown(curve)

    if not trades:
        metrics.warnings.append(
            "沒有任何交易。可能是訊號條件太嚴,也可能是資料不足以暖機。"
        )
        return metrics

    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]

    metrics.wins, metrics.losses = len(wins), len(losses)
    metrics.win_rate = len(wins) / len(trades) * 100

    metrics.gross_profit = sum(t.net_pnl for t in wins)
    metrics.gross_loss = abs(sum(t.net_pnl for t in losses))

    if metrics.gross_loss > 0:
        metrics.profit_factor = metrics.gross_profit / metrics.gross_loss
    elif metrics.gross_profit > 0:
        # 沒有任何虧損的樣本不代表 PF 無限大,而是樣本不足以評估
        metrics.warnings.append("沒有虧損交易,Profit Factor 無法評估")

    metrics.avg_win = metrics.gross_profit / len(wins) if wins else 0.0
    metrics.avg_loss = -metrics.gross_loss / len(losses) if losses else 0.0
    metrics.largest_win = max((t.net_pnl for t in wins), default=0.0)
    metrics.largest_loss = min((t.net_pnl for t in losses), default=0.0)

    if metrics.avg_loss != 0:
        metrics.payoff_ratio = abs(metrics.avg_win / metrics.avg_loss)

    # Expectancy:每一筆平均賺多少。比勝率重要得多。
    metrics.expectancy_usdt = sum(t.net_pnl for t in trades) / len(trades)

    r_multiples = [t.r_multiple for t in trades if t.r_multiple is not None]
    if r_multiples:
        metrics.expectancy_r = sum(r_multiples) / len(r_multiples)

    returns = [t.return_pct for t in trades]
    stdev = _stdev(returns)
    mean_return = sum(returns) / len(returns)

    if stdev and stdev > 0:
        metrics.sharpe = mean_return / stdev

    downside = [r for r in returns if r < 0]
    downside_dev = _stdev(downside) if len(downside) >= 2 else None
    if downside_dev and downside_dev > 0:
        metrics.sortino = mean_return / downside_dev

    # 年化:用實際涵蓋的時間,不是假設一年
    total_bars = result.bars or 0
    if total_bars > 0 and candle_hours > 0 and result.start_balance > 0:
        years = total_bars * candle_hours / HOURS_PER_YEAR
        if years > 0 and result.end_balance > 0:
            growth = result.end_balance / result.start_balance
            metrics.annualised_return_pct = (growth ** (1 / years) - 1) * 100

    if metrics.max_drawdown_pct > 0:
        if metrics.annualised_return_pct is not None:
            metrics.calmar = metrics.annualised_return_pct / metrics.max_drawdown_pct
        metrics.recovery_factor = metrics.total_return_pct / metrics.max_drawdown_pct

    metrics.avg_bars_held = sum(t.bars_held for t in trades) / len(trades)
    metrics.avg_mfe_pct = sum(t.mfe_pct for t in trades) / len(trades)
    metrics.avg_mae_pct = sum(t.mae_pct for t in trades) / len(trades)

    metrics.total_fees = sum(t.fees for t in trades)
    metrics.total_funding = sum(t.funding for t in trades)
    if result.start_balance > 0:
        metrics.cost_drag_pct = (
            (metrics.total_fees + metrics.total_funding) / result.start_balance * 100
        )

    for trade in trades:
        reason = trade.exit_reason or "未知"
        metrics.exit_reasons[reason] = metrics.exit_reasons.get(reason, 0) + 1
        if trade.direction.value == "做多":
            metrics.long_trades += 1
        else:
            metrics.short_trades += 1

    metrics.liquidations = metrics.exit_reasons.get("強制平倉", 0)

    # ---- 誠實性提醒 ----
    if metrics.total_trades < 30:
        metrics.warnings.append(
            f"只有 {metrics.total_trades} 筆交易,樣本太小,統計結果不可靠"
        )

    if metrics.liquidations:
        metrics.warnings.append(
            f"發生 {metrics.liquidations} 次強制平倉 —— 槓桿或停損設定有問題"
        )

    if metrics.expectancy_usdt <= 0:
        metrics.warnings.append(
            "Expectancy 非正值。即使勝率好看,這個策略長期是虧錢的。"
        )

    return metrics
