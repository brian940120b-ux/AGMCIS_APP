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


def run_lab():
    """
    Phase 7 起改用 agmcis.backtest 引擎:BingX 資料、下一根開盤成交、
    ATR 停損、手續費滑點資金費用、固定風險部位大小。

    報酬率會比舊版低很多。那不是策略變差,是舊版的數字本來就是假的。
    只看報酬率排名仍然不夠 —— 要看 Expectancy 與交易筆數,
    詳細指標請用 backtest.py 或 run_strategy_detailed()。
    """
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

if __name__ == "__main__":
    # Phase 1:原本整段在模組層執行,import 就會打網路跑完整回測。
    run_lab()
