"""
多標的 × 多策略的驗證掃描。

Phase 8 重寫。舊版做的事其實是:在一段固定歷史上跑每個策略,
按總報酬率排名,取第一名。那有四個問題:

  1. **沒有樣本外。** 排名完全建立在同一段資料上,第一名就是最會擬合
     那段歷史的那個。
  2. **失敗被記成 0%。** 例外被吃掉後以「0% 報酬」參與平均,
     把整體數字往上拉,而且看報告的人不知道有東西壞了。
  3. **按總報酬率排名。** 報酬率對少數幾筆極端交易極度敏感。
  4. **Survivorship bias。** 用「當前」成交量前 N 名回測過去,
     當時還沒上市或已經下市的標的完全不在樣本裡。

現在每個 (標的, 策略) 都走完整的 Lab 流程:
IS 調參 -> OOS 驗證 -> Walk Forward -> Monte Carlo -> Health Score。
排名依據是 OOS 期望值與 Health,不是報酬率。
沒通過的留在報告裡並附上被擋下來的理由。

第 4 點無法在這一層修掉 —— 需要包含已下市標的的歷史資料。
現在的做法是把它明確寫進輸出的 warnings,讓看報告的人知道這個限制還在。
"""
import logging

import strategies.breakout_strategy as breakout_strategy
import strategies.ema_strategy as ema_strategy
import strategies.rsi_strategy as rsi_strategy
from agmcis.backtest.costs import DEFAULT_COSTS
from agmcis.backtest.engine import BacktestEngine
from agmcis.backtest.legacy import WARMUP_BARS, build_signal_fns, load_data
from agmcis.lab import ensemble as ensemble_module
from agmcis.lab import evaluate as evaluate_module
from exchange_universe import get_top_volume_symbols

logger = logging.getLogger("agmcis.strategy_optimizer")

STRATEGIES = [
    ("EMA_RSI_MACD_PRO", ema_strategy),
    ("RSI_REVERSAL_PRO", rsi_strategy),
    ("BREAKOUT_PRO", breakout_strategy),
]

# 唯一可調的參數:ATR 停損倍數。
# 刻意只留一個 —— 參數越多越容易在歷史上擬合出漂亮的曲線。
ATR_STOP_MULTIPLES = [1.5, 2.0, 2.5, 3.0]

DEFAULT_TIMEFRAME = "1h"
DEFAULT_LIMIT = 1500
MONTE_CARLO_RUNS = 1000

SURVIVORSHIP_WARNING = (
    "標的清單是**當前**成交量前 N 名。當時還沒上市或已經下市的標的不在樣本裡,"
    "這是 survivorship bias,會讓整體結果偏樂觀。要修掉需要含已下市標的的歷史資料。"
)


def get_dynamic_symbols(limit=20):
    """⚠️ 這會打交易所 API 載入全部市場與 ticker,很慢。"""
    return [item["symbol"] for item in get_top_volume_symbols(limit=limit)]


def _param_sets():
    return [{"atr_stop_multiple": value} for value in ATR_STOP_MULTIPLES]


def _make_builders(df, strategy_module):
    """
    Lab 會把切好的 K 棒傳進來,但舊策略模組的指標是掛在 DataFrame 上的。
    這裡用「切片的第一根時間」對回原始 DataFrame 的位置,
    讓策略在子區間上也拿得到正確的指標值。
    """
    times = list(df["timestamp"])
    index_of_time = {int(t): i for i, t in enumerate(times)}

    def _offset(candles):
        if not candles:
            return 0
        first = candles[0]
        stamp = first.get("time") or first.get("timestamp") or 0
        return index_of_time.get(int(stamp), 0)

    def build_signal(candles, params):
        offset = _offset(candles)
        signal_fn, _ = build_signal_fns(
            df, strategy_module,
            atr_stop_multiple=params.get("atr_stop_multiple", 2.0),
        )
        return lambda history, index: signal_fn(history, index + offset)

    def build_exit(candles, params):
        offset = _offset(candles)
        _, exit_fn = build_signal_fns(
            df, strategy_module,
            atr_stop_multiple=params.get("atr_stop_multiple", 2.0),
        )
        return lambda history, index, position: exit_fn(
            history, index + offset, position,
        )

    return build_signal, build_exit


def evaluate_symbol(symbol, timeframe=DEFAULT_TIMEFRAME, limit=DEFAULT_LIMIT,
                    strategies=None, monte_carlo_runs=MONTE_CARLO_RUNS, seed=None):
    """回傳這個標的上每個策略的 StrategyEvaluation。"""
    strategies = strategies or STRATEGIES
    df = load_data(symbol, timeframe=timeframe, limit=limit)
    candles = df.to_dict("records")

    evaluations = []
    for name, module in strategies:
        build_signal, build_exit = _make_builders(df, module)

        evaluation = evaluate_module.evaluate(
            candles, build_signal, _param_sets(),
            name=name, symbol=symbol,
            monte_carlo_runs=monte_carlo_runs, seed=seed,
            warmup=WARMUP_BARS,
            exit_fn_builder=build_exit,
            engine=BacktestEngine(costs=DEFAULT_COSTS),
        )
        evaluations.append(evaluation)

    return evaluations


def _row(evaluation):
    validation = evaluation.validation
    oos = validation.oos_run if validation else None
    metrics = oos.metrics if (oos and oos.ok) else None

    return {
        "symbol": evaluation.symbol,
        "strategy": evaluation.name,
        "verdict": evaluation.verdict,
        "params": evaluation.chosen_params,
        "oos_trades": metrics.total_trades if metrics else 0,
        "oos_expectancy_r": metrics.expectancy_r if metrics else None,
        "oos_profit_factor": metrics.profit_factor if metrics else None,
        "oos_return_pct": metrics.total_return_pct if metrics else None,
        "oos_max_drawdown_pct": metrics.max_drawdown_pct if metrics else None,
        "health_score": evaluation.health.score if evaluation.health else 0.0,
        "walk_forward_consistency": (
            evaluation.walk_forward.consistency if evaluation.walk_forward else None
        ),
        "risk_of_ruin": (
            evaluation.monte_carlo.risk_of_ruin if evaluation.monte_carlo else None
        ),
        "blockers": list(evaluation.health.blockers) if evaluation.health else [],
        "error": evaluation.error,
    }


def _rank_key(row):
    """排名依據:通過與否 > Health > OOS 期望值。報酬率不參與排名。"""
    verdict_rank = {"PASS": 0, "MARGINAL": 1, "REJECT": 2, "ERROR": 3}
    return (
        verdict_rank.get(row["verdict"], 3),
        -(row["health_score"] or 0),
        -(row["oos_expectancy_r"] or 0),
    )


def get_strategy_optimizer(symbols=None, limit=20, timeframe=DEFAULT_TIMEFRAME,
                           candles=DEFAULT_LIMIT, seed=None):
    if symbols is None:
        symbols = get_dynamic_symbols(limit=limit)

    results = []
    symbol_best = []
    all_evaluations = []
    errors = []

    for symbol in symbols:
        try:
            evaluations = evaluate_symbol(
                symbol, timeframe=timeframe, limit=candles, seed=seed,
            )
        except Exception as exc:
            # 失敗就是失敗,不記成 0%。舊版把它當成中性結果放進平均。
            logger.exception("標的驗證失敗 | %s", symbol)
            message = f"{type(exc).__name__}: {exc}"
            errors.append({"symbol": symbol, "error": message})
            results.append({
                "symbol": symbol, "strategy": None, "verdict": "ERROR",
                "error": message, "health_score": 0.0,
                "oos_expectancy_r": None, "oos_trades": 0,
            })
            symbol_best.append({
                "symbol": symbol, "strategy": None,
                "verdict": "ERROR", "error": message,
            })
            continue

        all_evaluations.extend(evaluations)
        rows = sorted((_row(e) for e in evaluations), key=_rank_key)
        results.extend(rows)

        passed = [r for r in rows if r["verdict"] in ("PASS", "MARGINAL")]
        symbol_best.append(passed[0] if passed else {
            "symbol": symbol,
            "strategy": None,
            "verdict": "REJECT",
            "error": None,
            "blockers": rows[0]["blockers"] if rows else [],
        })

    return {
        "all_results": sorted(results, key=_rank_key),
        "symbol_best": symbol_best,
        "strategy_summary": _summarise_by_strategy(results),
        "best_overall": _best_overall(results),
        "ensemble": ensemble_module.build(all_evaluations).to_dict(),
        "errors": errors,
        "warnings": [SURVIVORSHIP_WARNING],
    }


def _summarise_by_strategy(results):
    """
    按策略彙總。分母是**實際驗證成功的次數**,不包含出錯的標的 ——
    把錯誤算進分母會稀釋通過率,算成 0% 又會拉低平均,兩種都在說謊。
    """
    summary = {}

    for row in results:
        name = row.get("strategy")
        if not name:
            continue

        entry = summary.setdefault(name, {
            "strategy": name, "evaluated": 0, "passed": 0,
            "marginal": 0, "rejected": 0, "errors": 0,
            "_health_total": 0.0,
        })

        if row["verdict"] == "ERROR":
            entry["errors"] += 1
            continue

        entry["evaluated"] += 1
        entry["_health_total"] += row.get("health_score") or 0.0

        if row["verdict"] == "PASS":
            entry["passed"] += 1
        elif row["verdict"] == "MARGINAL":
            entry["marginal"] += 1
        else:
            entry["rejected"] += 1

    output = []
    for entry in summary.values():
        evaluated = entry.pop("_health_total"), entry["evaluated"]
        health_total, count = evaluated
        entry["avg_health"] = round(health_total / count, 2) if count else 0.0
        entry["pass_rate"] = round(entry["passed"] / count, 4) if count else 0.0
        output.append(entry)

    output.sort(key=lambda x: (-x["passed"], -x["avg_health"]))
    return output


def _best_overall(results):
    """全部都沒通過就回 None —— 不從被拒絕的裡面挑一個「比較不差」的。"""
    usable = [r for r in results if r["verdict"] in ("PASS", "MARGINAL")]
    if not usable:
        return None
    return sorted(usable, key=_rank_key)[0]
