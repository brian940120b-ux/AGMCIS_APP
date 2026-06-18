from backtest_engine import (
    load_data,
    run_strategy
)

import strategies.ema_strategy as ema_strategy
import strategies.rsi_strategy as rsi_strategy
import strategies.breakout_strategy as breakout_strategy


symbols = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "DOGE/USDT"
]

strategies = [
    ("EMA_RSI_MACD_PRO", ema_strategy),
    ("RSI_REVERSAL_PRO", rsi_strategy),
    ("BREAKOUT_PRO", breakout_strategy)
]


print()
print("========== AGMCIS V9.5 PRO 多幣種回測 ==========")

for symbol in symbols:
    print()
    print(f"----- {symbol} -----")

    try:
        df = load_data(symbol)

        results = []

        for name, strategy in strategies:
            capital = run_strategy(df, strategy)

            return_pct = (
                (capital - 10000)
                / 10000
                * 100
            )

            results.append(
                (name, return_pct)
            )

        results.sort(
            key=lambda x: x[1],
            reverse=True
        )

        for rank, result in enumerate(results):
            print(
                f"{rank + 1}. "
                f"{result[0]} "
                f"{result[1]:.2f}%"
            )

    except Exception as e:
        print(f"{symbol} 回測失敗：{e}")

print()
print("==============================================")