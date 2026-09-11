"""
把回測引擎跑在切分好的資料上。

三個入口:

  run_on_slice()          跑一段資料
  in_sample_out_of_sample() 調參用前段、驗證用後段
  walk_forward()          滾動地「用過去調參、在未來驗證」

共同的鐵律:

  * **參數只能用訓練段挑。** 用測試段挑參數等於沒有測試段。
  * **所有參數組合的結果都要留著。** 只報告最好的那組就是 cherry-picking。
  * **失敗的組合不隱藏。** 例外要記錄成 error,不能吃掉變成 0%。
"""
import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from agmcis.backtest import metrics as metrics_module
from agmcis.backtest.costs import DEFAULT_COSTS
from agmcis.backtest.engine import BacktestEngine
from agmcis.lab import scoring, splits as splits_module

logger = logging.getLogger("agmcis.lab.runner")

DEFAULT_WARMUP = 60


@dataclass
class SliceRun:
    """一組參數在一段資料上的結果。"""
    params: Dict = field(default_factory=dict)
    label: str = ""
    metrics: Optional[object] = None
    result: Optional[object] = None
    score: Optional[float] = None
    error: Optional[str] = None

    @property
    def ok(self):
        return self.error is None and self.metrics is not None

    def to_dict(self):
        return {
            "label": self.label,
            "params": dict(self.params),
            "score": self.score,
            "error": self.error,
            "metrics": self.metrics.to_dict() if self.metrics else None,
        }


def run_on_slice(candles, build_signal_fn, params=None, label="",
                 engine=None, warmup=DEFAULT_WARMUP, candle_hours=1.0,
                 symbol="LAB", exit_fn_builder=None):
    """
    在一段 K 棒上跑一次回測。

    build_signal_fn(candles, params) -> signal_fn
        由呼叫端決定參數怎麼變成訊號函式。Lab 不猜策略長什麼樣子。

    回傳 SliceRun。回測本身失敗時 error 會有值 ——
    **不會**安靜地回傳一個 0% 的結果。舊的 strategy_optimizer 就是那樣做的,
    於是壞掉的標的會以「0% 報酬」參與平均,把整體數字往上拉。
    """
    params = dict(params or {})
    run = SliceRun(params=params, label=label)

    if engine is None:
        engine = BacktestEngine(costs=DEFAULT_COSTS, candle_hours=candle_hours)

    try:
        signal_fn = build_signal_fn(candles, params)
        exit_fn = exit_fn_builder(candles, params) if exit_fn_builder else None

        result = engine.run(
            candles, signal_fn, symbol=symbol, warmup=warmup, exit_fn=exit_fn,
        )
        run.result = result
        run.metrics = metrics_module.compute(result, candle_hours=candle_hours)
        run.score = scoring.objective(run.metrics)

    except Exception as exc:
        logger.exception("回測失敗 | %s | params=%s", label, params)
        run.error = f"{type(exc).__name__}: {exc}"

    return run


def optimise(candles, build_signal_fn, param_sets, label="", **kwargs):
    """
    在**同一段**資料上試過所有參數組合,回傳全部結果(依分數排序)。

    回傳的是 list,不是單一個最佳解 —— 呼叫端必須看得到完整分佈。
    只看第一名會看不到「第一名只比第二名好一點點,但參數天差地遠」
    這種典型的過擬合訊號。
    """
    runs = [
        run_on_slice(candles, build_signal_fn, params,
                     label=f"{label}#{i}" if label else f"#{i}", **kwargs)
        for i, params in enumerate(param_sets)
    ]

    runs.sort(key=lambda r: (r.score is None, -(r.score or 0)))
    return runs


def best_of(runs):
    """取分數最高且沒出錯的那一組。全部失敗時回 None。"""
    for run in runs:
        if run.ok and run.score is not None:
            return run
    return None


@dataclass
class ValidationReport:
    label: str = ""
    train_runs: List[SliceRun] = field(default_factory=list)
    chosen_params: Optional[Dict] = None
    is_run: Optional[SliceRun] = None
    oos_run: Optional[SliceRun] = None
    degradation: Optional[float] = None
    warnings: List[str] = field(default_factory=list)

    def to_dict(self):
        return {
            "label": self.label,
            "chosen_params": self.chosen_params,
            "in_sample": self.is_run.to_dict() if self.is_run else None,
            "out_of_sample": self.oos_run.to_dict() if self.oos_run else None,
            "degradation": self.degradation,
            "all_train_runs": [r.to_dict() for r in self.train_runs],
            "warnings": list(self.warnings),
        }


def in_sample_out_of_sample(candles, build_signal_fn, param_sets,
                            oos_ratio=0.3, warmup=DEFAULT_WARMUP, **kwargs):
    """
    前段調參、後段驗證。

    OOS 那一段**只跑一次**,而且是用訓練段挑出來的參數跑的。
    如果拿 OOS 結果回頭換參數再跑一次,OOS 就被污染了,
    之後看到的所有數字都不再是樣本外。
    """
    split = splits_module.in_sample_out_of_sample(len(candles), oos_ratio)
    report = ValidationReport(label="IS/OOS")

    train_candles = split.train_slice(candles)
    test_candles = split.test_slice(candles)

    if len(test_candles) <= warmup:
        report.warnings.append(
            f"OOS 只有 {len(test_candles)} 根,扣掉暖機 {warmup} 根後不夠回測。"
        )
        return report

    report.train_runs = optimise(
        train_candles, build_signal_fn, param_sets,
        label="IS", warmup=warmup, **kwargs
    )

    winner = best_of(report.train_runs)
    if winner is None:
        report.warnings.append("訓練段沒有任何一組參數產生有效結果。")
        return report

    report.chosen_params = dict(winner.params)
    report.is_run = winner
    report.oos_run = run_on_slice(
        test_candles, build_signal_fn, winner.params,
        label="OOS", warmup=warmup, **kwargs
    )

    if report.oos_run.ok:
        report.degradation = scoring.expectancy_degradation(
            winner.metrics, report.oos_run.metrics,
        )

    _warn_on_flat_optimum(report)
    return report


def _warn_on_flat_optimum(report):
    """
    第一名跟第二名差不多,但參數差很多 —— 代表這個「最佳解」是雜訊挑出來的。
    """
    usable = [r for r in report.train_runs if r.ok and r.score is not None]
    if len(usable) < 2:
        return

    best, second = usable[0].score, usable[1].score
    if best <= 0:
        return

    if abs(best - second) / abs(best) < 0.05:
        report.warnings.append(
            "訓練段第一名與第二名的分數相差不到 5%,"
            "這個「最佳參數」很可能只是雜訊。"
        )


@dataclass
class WalkForwardReport:
    windows: List[ValidationReport] = field(default_factory=list)
    oos_runs: List[SliceRun] = field(default_factory=list)
    consistency: Optional[float] = None
    median_degradation: Optional[float] = None
    warnings: List[str] = field(default_factory=list)

    @property
    def profitable_windows(self):
        return len([
            r for r in self.oos_runs
            if r.ok and r.metrics.expectancy_r is not None
            and r.metrics.expectancy_r > 0
        ])

    def to_dict(self):
        return {
            "windows": [w.to_dict() for w in self.windows],
            "window_count": len(self.windows),
            "profitable_windows": self.profitable_windows,
            "consistency": self.consistency,
            "median_degradation": self.median_degradation,
            "warnings": list(self.warnings),
        }

    def summary_lines(self):
        lines = [
            f"Walk Forward 視窗  {len(self.windows)} 段",
            f"OOS 正期望值視窗   {self.profitable_windows} / {len(self.oos_runs)}",
        ]
        if self.consistency is not None:
            lines.append(f"一致性             {self.consistency * 100:.0f}%")
        if self.median_degradation is not None:
            lines.append(f"退化中位數         {self.median_degradation * 100:+.0f}%")
        return lines


def walk_forward(candles, build_signal_fn, param_sets,
                 train_size, test_size, step=None, anchored=False,
                 warmup=DEFAULT_WARMUP, **kwargs):
    """
    滾動驗證。每一段都重新用自己的訓練視窗挑參數,再在緊接著的
    測試視窗上驗證 —— 這模擬的是真實使用方式:你只會用過去的資料調參。

    看的是**一致性**:多數視窗都有正期望值,比單一視窗的漂亮數字更有意義。
    只有一段特別好、其他都不行,那是運氣。
    """
    windows = splits_module.walk_forward(
        len(candles), train_size, test_size, step=step, anchored=anchored,
    )
    report = WalkForwardReport()

    for split in windows:
        window = ValidationReport(label=split.label)

        train_candles = split.train_slice(candles)
        test_candles = split.test_slice(candles)

        if len(test_candles) <= warmup or len(train_candles) <= warmup:
            window.warnings.append(
                f"{split.label} 視窗扣掉暖機 {warmup} 根後資料不足,略過。"
            )
            report.windows.append(window)
            continue

        window.train_runs = optimise(
            train_candles, build_signal_fn, param_sets,
            label=f"{split.label}-IS", warmup=warmup, **kwargs
        )

        winner = best_of(window.train_runs)
        if winner is None:
            window.warnings.append(f"{split.label} 訓練段沒有有效結果。")
            report.windows.append(window)
            continue

        window.chosen_params = dict(winner.params)
        window.is_run = winner
        window.oos_run = run_on_slice(
            test_candles, build_signal_fn, winner.params,
            label=f"{split.label}-OOS", warmup=warmup, **kwargs
        )

        if window.oos_run.ok:
            window.degradation = scoring.expectancy_degradation(
                winner.metrics, window.oos_run.metrics,
            )
            report.oos_runs.append(window.oos_run)

        report.windows.append(window)

    _summarise_walk_forward(report)
    return report


def _summarise_walk_forward(report):
    if not report.oos_runs:
        report.warnings.append("沒有任何視窗產生有效的 OOS 結果。")
        return

    report.consistency = round(
        report.profitable_windows / len(report.oos_runs), 4,
    )

    degradations = sorted(
        w.degradation for w in report.windows if w.degradation is not None
    )
    if degradations:
        middle = len(degradations) // 2
        report.median_degradation = round(
            degradations[middle] if len(degradations) % 2
            else (degradations[middle - 1] + degradations[middle]) / 2,
            4,
        )

    if report.consistency < 0.5:
        report.warnings.append(
            f"只有 {report.consistency * 100:.0f}% 的視窗有正期望值 —— "
            f"這個策略不穩定,單一視窗的好結果是運氣。"
        )

    chosen = [
        tuple(sorted(w.chosen_params.items()))
        for w in report.windows if w.chosen_params
    ]
    if len(set(chosen)) == len(chosen) and len(chosen) > 2:
        report.warnings.append(
            "每個視窗挑出來的最佳參數都不一樣 —— "
            "代表參數在跟雜訊對齊,不是在抓真實規律。"
        )
