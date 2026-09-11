"""
單一策略的完整驗證流程。

    參數搜尋(只用 IS) -> OOS 驗證 -> Walk Forward -> Monte Carlo -> Health Score

這是「這個策略能不能上線」的唯一入口。任何繞過它的結論都只是
在一段固定歷史上的曲線,不是證據。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from agmcis.lab import montecarlo, runner, scoring


@dataclass
class StrategyEvaluation:
    name: str = ""
    symbol: str = ""
    validation: Optional[runner.ValidationReport] = None
    walk_forward: Optional[runner.WalkForwardReport] = None
    monte_carlo: Optional[montecarlo.MonteCarloResult] = None
    health: Optional[scoring.HealthScore] = None
    error: Optional[str] = None

    @property
    def verdict(self):
        if self.error:
            return "ERROR"
        return self.health.verdict if self.health else "REJECT"

    @property
    def chosen_params(self):
        return self.validation.chosen_params if self.validation else None

    def to_dict(self):
        return {
            "name": self.name,
            "symbol": self.symbol,
            "verdict": self.verdict,
            "chosen_params": self.chosen_params,
            "error": self.error,
            "validation": self.validation.to_dict() if self.validation else None,
            "walk_forward": self.walk_forward.to_dict() if self.walk_forward else None,
            "monte_carlo": self.monte_carlo.to_dict() if self.monte_carlo else None,
            "health": self.health.to_dict() if self.health else None,
        }

    def summary_lines(self):
        lines = [f"=== {self.name} | {self.symbol} ==="]

        if self.error:
            lines.append(f"  ⛔ {self.error}")
            return lines

        if self.validation and self.validation.oos_run and self.validation.oos_run.ok:
            lines.append("  [OOS]")
            lines.extend(
                "    " + line
                for line in self.validation.oos_run.metrics.summary_lines()
            )

        if self.walk_forward:
            lines.extend("  " + line for line in self.walk_forward.summary_lines())

        if self.monte_carlo and self.monte_carlo.runs:
            lines.extend("  " + line for line in self.monte_carlo.summary_lines())

        if self.health:
            lines.extend("  " + line for line in self.health.summary_lines())

        return lines


def evaluate(candles, build_signal_fn, param_sets, name="", symbol="",
             oos_ratio=0.3, walk_forward_train=None, walk_forward_test=None,
             monte_carlo_runs=montecarlo.DEFAULT_RUNS,
             risk_pct=montecarlo.DEFAULT_RISK_PCT, seed=None, **kwargs):
    """
    param_sets 只有一組時就是純驗證(沒有參數搜尋),流程其餘不變。

    walk_forward_train / walk_forward_test 給 None 時自動從資料長度推算;
    資料不夠跑 Walk Forward 就跳過並留下警告,而不是硬跑出一段假結果。
    """
    evaluation = StrategyEvaluation(name=name, symbol=symbol)

    if not param_sets:
        evaluation.error = "沒有給任何參數組合。"
        return evaluation

    try:
        evaluation.validation = runner.in_sample_out_of_sample(
            candles, build_signal_fn, param_sets, oos_ratio=oos_ratio, **kwargs
        )
    except Exception as exc:
        evaluation.error = f"IS/OOS 失敗:{type(exc).__name__}: {exc}"
        return evaluation

    oos_run = evaluation.validation.oos_run
    is_run = evaluation.validation.is_run

    # --- Walk Forward ---
    train_size, test_size = _walk_forward_sizes(
        len(candles), walk_forward_train, walk_forward_test,
    )
    if train_size is None:
        evaluation.walk_forward = runner.WalkForwardReport(
            warnings=[f"資料只有 {len(candles)} 根,不足以做 Walk Forward。"],
        )
    else:
        try:
            evaluation.walk_forward = runner.walk_forward(
                candles, build_signal_fn, param_sets,
                train_size=train_size, test_size=test_size, **kwargs
            )
        except Exception as exc:
            evaluation.walk_forward = runner.WalkForwardReport(
                warnings=[f"Walk Forward 失敗:{type(exc).__name__}: {exc}"],
            )

    # --- Monte Carlo(用 OOS 的交易)---
    if oos_run is not None and oos_run.ok:
        evaluation.monte_carlo = montecarlo.run(
            oos_run.result, runs=monte_carlo_runs,
            risk_pct=risk_pct, seed=seed,
        )

    # --- Health Score ---
    evaluation.health = scoring.health_score(
        oos_metrics=oos_run.metrics if (oos_run and oos_run.ok) else None,
        is_metrics=is_run.metrics if (is_run and is_run.ok) else None,
        monte_carlo=evaluation.monte_carlo,
    )

    _fold_walk_forward_into_health(evaluation)
    return evaluation


def _walk_forward_sizes(total, train_size, test_size, min_windows=3):
    """
    自動推算視窗大小:訓練 3 份、測試 1 份,至少要切得出 min_windows 段。
    切不出來就回 (None, None) —— 硬切出兩段沒有意義。
    """
    if train_size and test_size:
        return train_size, test_size

    unit = total // (3 + min_windows)
    if unit < 80:          # 暖機 60 根之後至少要留一些 K 棒
        return None, None

    return unit * 3, unit


def _fold_walk_forward_into_health(evaluation):
    """Walk Forward 的一致性不加分,但不一致會擋下來。"""
    report = evaluation.walk_forward
    health = evaluation.health

    if report is None or health is None:
        return

    for warning in report.warnings:
        health.warnings.append(f"Walk Forward:{warning}")

    if report.consistency is not None and report.consistency < 0.5:
        health.blockers.append(
            f"Walk Forward 只有 {report.consistency * 100:.0f}% 的視窗有正期望值。"
        )
        health.verdict = "REJECT"
