"""
出場參數調校(Master Prompt 第五十六節)。

第五十六節說 TP/SL 不該全部用固定百分比,可以用 ATR、市場結構、
支撐壓力、波動度、流動性、風險報酬比 —— 然後加了一句最重要的:

    **但必須由回測驗證。**

`agmcis/execution/exit_plan.py` 現在的預設值(1R / 2R / 3R 分批
30/30/40、ATR 2.0 倍移動停損、72 小時時間出場)是**起點不是結論**。
這個模組是拿來把它們變成結論的工具。

## 這個模組回答的問題

「換一組出場參數,同一批進場訊號的結果會怎樣?」

刻意只換出場:進場訊號固定,所以差異全部來自出場。
同時換進場與出場,結果好了也說不出是哪一邊的功勞。

## 三個防過度最佳化的機制(第三十六節)

**一、每一組都回報樣本數,而且樣本不足的不參與排名。**
在 40 筆交易上挑出最好的一組參數,挑到的是那 40 筆的噪音。

**二、排名用期望值 R 而不是總報酬。**
總報酬會被少數幾筆極端獲利主宰,而那幾筆通常不會重演。

**三、回報「最好」與「中位數」的差距。**
差距很大代表這個參數面很崎嶇 —— 崎嶇的參數面上的最高點,
幾乎一定是過度擬合。差距小代表怎麼設都差不多,那才是穩健。

## 它不會自己套用結果

第七十八節禁止「自己修改 → 自己測試 → 自己批准 → 自己 Live」。
這個模組只產生報告。要改預設值,走
`agmcis/review/proposals.py` 的提案流程,由人批准。
"""
import logging
import statistics
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger("agmcis.lab.exit_tuning")

# 樣本不足的組合不參與排名。與 attribution.MIN_SAMPLE 同一個數字 ——
# 兩邊用不同的門檻,會讓同一批交易在兩份報告裡一份可信一份不可信。
MIN_TRADES = 20

# 預設要試哪幾組。刻意包含「什麼都不做」(None / None)當基準線 ——
# 沒有基準線就無法回答「加了移動停損到底有沒有比較好」。
DEFAULT_TRAILING = (
    None,
    {"mode": "percent", "gap_pct": 2.0},
    {"mode": "percent", "gap_pct": 3.0},
    {"mode": "percent", "gap_pct": 5.0},
)

DEFAULT_STAGES = (
    None,                              # 一次全平
    ((1.0, 0.5), (2.0, 0.5)),
    ((1.0, 0.3), (2.0, 0.3), (3.0, 0.4)),   # 現在的預設
    ((1.5, 0.5), (3.0, 0.5)),
)


def _label(trailing, stages):
    trail = "無移動停損" if not trailing else (
        f"{trailing.get('mode')}"
        + (f" {trailing.get('gap_pct')}%" if trailing.get("gap_pct") else "")
        + (f" {trailing.get('multiple')}xATR" if trailing.get("multiple") else "")
    )
    tp = "一次全平" if not stages else " / ".join(
        f"{r:g}R×{int(p * 100)}%" for r, p in stages
    )
    return f"{trail} + {tp}"


@dataclass
class ExitVariant:
    label: str = ""
    trailing: Optional[dict] = None
    stages: Optional[tuple] = None
    trades: int = 0
    # None = 算不出來(沒有交易,或停損距離是 0)。**不是 0** ——
    # 0 代表「打平」,而打平與「沒有答案」是兩件不同的事,
    # 而且前者可以排名、後者不行。
    expectancy_r: Optional[float] = None
    profit_factor: Optional[float] = None
    win_rate: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    error: Optional[str] = None

    @property
    def reliable(self):
        return (
            self.error is None
            and self.expectancy_r is not None
            and self.trades >= MIN_TRADES
        )

    def to_dict(self):
        return {
            "label": self.label,
            "trailing": self.trailing,
            "stages": list(self.stages) if self.stages else None,
            "trades": self.trades,
            "expectancy_r": (
                round(self.expectancy_r, 4)
                if self.expectancy_r is not None else None
            ),
            "profit_factor": (
                round(self.profit_factor, 3)
                if self.profit_factor is not None else None
            ),
            "win_rate": round(self.win_rate, 2),
            "total_return_pct": round(self.total_return_pct, 3),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "reliable": self.reliable,
            "error": self.error,
        }


@dataclass
class ExitTuning:
    variants: List[ExitVariant] = field(default_factory=list)
    baseline: Optional[dict] = None
    best: Optional[dict] = None
    spread: Optional[float] = None
    verdict: str = "UNKNOWN"
    warnings: List[str] = field(default_factory=list)

    def to_dict(self):
        return {
            "variants": [v.to_dict() for v in self.variants],
            "baseline": self.baseline,
            "best": self.best,
            "spread": (round(self.spread, 4) if self.spread is not None else None),
            "verdict": self.verdict,
            "warnings": list(self.warnings),
            "min_trades": MIN_TRADES,
        }

    def summary_lines(self):
        lines = ["出場參數(組合 / 筆數 / 期望值R / PF / 樣本)"]
        for v in self.variants:
            pf = f"{v.profit_factor:.2f}" if v.profit_factor is not None else "—"
            exp = (
                f"{v.expectancy_r:+7.3f}"
                if v.expectancy_r is not None else "      —"
            )
            lines.append(
                f"  {v.label:<34} {v.trades:>4}  "
                f"{exp}  PF {pf:>5}  "
                f"{'足夠' if v.reliable else '不足'}"
                + (f"  [{v.error}]" if v.error else "")
            )

        if self.best:
            lines.append(
                f"最佳:{self.best['label']}(期望值 R "
                f"{self.best['expectancy_r']:+.3f})"
            )
        else:
            lines.append("沒有樣本足夠的組合,不下結論。")

        if self.spread is not None:
            lines.append(
                f"最佳與中位數的差距:{self.spread:+.3f} R —— "
                + ("參數面崎嶇,最高點很可能是過度擬合。"
                   if self.spread > FLAT_SPREAD else "參數面平坦,結論相對穩健。")
            )

        lines.append(f"判定:{self.verdict}")
        for warning in self.warnings:
            lines.append(f"  ⚠️  {warning}")
        return lines


# 最佳與中位數差距超過這個值,就當成參數面崎嶇。
# 0.15R 的意思是:換一組參數,每筆交易的期望值差了停損距離的 15%。
FLAT_SPREAD = 0.15

VERDICT_ROBUST = "ROBUST"          # 怎麼設都差不多 —— 可以放心用預設值
VERDICT_SENSITIVE = "SENSITIVE"    # 差很多 —— 最高點很可能是擬合出來的
VERDICT_INSUFFICIENT = "INSUFFICIENT_DATA"


def run(run_backtest, trailing_options=DEFAULT_TRAILING,
        stage_options=DEFAULT_STAGES):
    """
    每一組出場參數跑一次回測。

    `run_backtest(trailing, stages) -> BacktestResult`,由呼叫端提供 ——
    這個模組不知道要用哪些 K 棒、哪個策略。與 cost_sensitivity 同一個
    形狀,理由也相同。

    **一組失敗不會中斷其他組**,但失敗會留在報告裡。
    悄悄跳過失敗的組合,會讓「這一組沒出現」被讀成「這一組比較差」。
    """
    from agmcis.backtest import metrics as metrics_module

    output = ExitTuning()

    for trailing in trailing_options:
        for stages in stage_options:
            variant = ExitVariant(
                label=_label(trailing, stages),
                trailing=trailing, stages=stages,
            )

            try:
                result = run_backtest(trailing, stages)
                stats = metrics_module.compute(result)
            except Exception as exc:
                logger.exception("出場調校:%s 失敗", variant.label)
                variant.error = f"{type(exc).__name__}: {exc}"
                output.variants.append(variant)
                continue

            variant.trades = stats.total_trades
            variant.expectancy_r = stats.expectancy_r
            variant.profit_factor = stats.profit_factor
            variant.win_rate = stats.win_rate
            variant.total_return_pct = stats.total_return_pct
            variant.max_drawdown_pct = stats.max_drawdown_pct
            output.variants.append(variant)

    _conclude(output)
    return output


def _conclude(output):
    failed = [v for v in output.variants if v.error]
    if failed:
        output.warnings.append(
            f"{len(failed)} 組回測失敗,沒有納入排名:"
            + "、".join(v.label for v in failed[:3])
        )

    # 基準線:什麼都不加的那一組。它是「加了東西到底有沒有比較好」
    # 的唯一參照。
    for variant in output.variants:
        if variant.trailing is None and variant.stages is None:
            output.baseline = variant.to_dict()
            break

    if output.baseline is None:
        output.warnings.append(
            "沒有跑「無移動停損 + 一次全平」的基準線,"
            "無法判斷加上出場機制到底有沒有比較好。"
        )

    usable = [v for v in output.variants if v.reliable]

    if not usable:
        output.verdict = VERDICT_INSUFFICIENT
        output.warnings.append(
            f"沒有任何一組達到 {MIN_TRADES} 筆。在這麼少的樣本上挑參數,"
            f"挑到的是噪音不是優勢。"
        )
        return

    ranked = sorted(usable, key=lambda v: v.expectancy_r, reverse=True)
    output.best = ranked[0].to_dict()

    expectancies = [v.expectancy_r for v in usable]
    output.spread = ranked[0].expectancy_r - statistics.median(expectancies)

    output.verdict = (
        VERDICT_SENSITIVE if output.spread > FLAT_SPREAD else VERDICT_ROBUST
    )

    if output.verdict == VERDICT_SENSITIVE:
        output.warnings.append(
            "最佳組合明顯優於中位數。這通常代表過度擬合,"
            "不是找到了更好的參數 —— 換一段時間重跑再說。"
        )

    baseline_r = (output.baseline or {}).get("expectancy_r")
    if baseline_r is not None and output.best:
        gain = output.best["expectancy_r"] - baseline_r
        if gain <= 0:
            output.warnings.append(
                "沒有任何一組贏過「什麼都不加」的基準線。"
                "分批停利與移動停損在這批資料上沒有幫助。"
            )
