from backtest_engine import (
    load_data,
    run_strategy
)

import strategies.ema_strategy as ema_strategy
import strategies.rsi_strategy as rsi_strategy
import strategies.breakout_strategy as breakout_strategy


SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "DOGE/USDT"
]


STRATEGIES = [
    ("EMA_RSI_MACD_PRO", ema_strategy),
    ("RSI_REVERSAL_PRO", rsi_strategy),
    ("BREAKOUT_PRO", breakout_strategy)
]


def get_strategy_optimizer():
    results = []
    symbol_best = []

    for symbol in SYMBOLS:
        try:
            df = load_data(symbol)

            local_results = []

            for strategy_name, strategy_module in STRATEGIES:
                capital = run_strategy(
                    df,
                    strategy_module
                )

                return_pct = (
                    (capital - 10000)
                    / 10000
                    * 100
                )

                item = {
                    "symbol": symbol,
                    "strategy": strategy_name,
                    "return_pct": round(return_pct, 2),
                    "capital": round(capital, 2)
                }

                results.append(item)
                local_results.append(item)

            local_results.sort(
                key=lambda x: x["return_pct"],
                reverse=True
            )

            symbol_best.append(local_results[0])

        except Exception as e:
            symbol_best.append({
                "symbol": symbol,
                "strategy": "ERROR",
                "return_pct": 0,
                "capital": 10000,
                "error": str(e)
            })

    results.sort(
        key=lambda x: x["return_pct"],
        reverse=True
    )

    strategy_map = {}

    for item in results:
        strategy = item["strategy"]

        if strategy not in strategy_map:
            strategy_map[strategy] = {
                "strategy": strategy,
                "count": 0,
                "total_return": 0
            }

        strategy_map[strategy]["count"] += 1
        strategy_map[strategy]["total_return"] += item["return_pct"]

    strategy_summary = []

    for strategy, data in strategy_map.items():
        avg_return = data["total_return"] / data["count"]

        strategy_summary.append({
            "strategy": strategy,
            "avg_return": round(avg_return, 2),
            "tested_symbols": data["count"]
        })

    strategy_summary.sort(
        key=lambda x: x["avg_return"],
        reverse=True
    )

    best_overall = strategy_summary[0] if strategy_summary else {
        "strategy": "-",
        "avg_return": 0,
        "tested_symbols": 0
    }

    return {
        "all_results": results,
        "symbol_best": symbol_best,
        "strategy_summary": strategy_summary,
        "best_overall": best_overall
    }