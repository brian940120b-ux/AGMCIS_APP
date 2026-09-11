"""
單一策略回測報表(命令列用)。

Phase 7 重寫。舊版有五個問題,每一個都讓結果偏樂觀:

  * 資料抓 Binance,實際下單在 BingX —— 價格與流動性都不是成交環境。
  * 訊號與成交在同一根 K 棒的收盤價 —— 偷看未來。
  * 停損停利只比對 close,盤中穿刺不算 —— 系統性高估勝率。
  * 只有固定 0.1% 手續費,沒有滑點、點差、資金費用、強平。
  * 每筆押上 100% 資金完全複利。

現在整段轉接到 agmcis.backtest,並且輸出完整指標而不只是勝率與報酬率。
Expectancy 與 Profit Factor 才是判斷策略有沒有優勢的依據,
勝率高但期望值為負的策略照樣會把帳戶打光。

用法:
    python backtest.py [SYMBOL] [TIMEFRAME]
"""
import sys

from agmcis.backtest import metrics as metrics_module
from agmcis.backtest.costs import DEFAULT_COSTS
from agmcis.backtest.legacy import load_data, run_strategy_detailed
import strategies.ema_strategy as ema_strategy

START_CAPITAL = 10000.0
RISK_PER_TRADE_PCT = 1.0
MAX_LEVERAGE = 3.0

TIMEFRAME_HOURS = {
    "1m": 1 / 60, "5m": 5 / 60, "15m": 0.25, "30m": 0.5,
    "1h": 1.0, "2h": 2.0, "4h": 4.0, "1d": 24.0,
}


def run_backtest(symbol="BTC/USDT", timeframe="1h", limit=1500,
                 strategy_module=ema_strategy):
    df = load_data(symbol, timeframe=timeframe, limit=limit)
    candle_hours = TIMEFRAME_HOURS.get(timeframe, 1.0)

    result = run_strategy_detailed(
        df, strategy_module,
        start_balance=START_CAPITAL,
        costs=DEFAULT_COSTS,
        risk_per_trade_pct=RISK_PER_TRADE_PCT,
        max_leverage=MAX_LEVERAGE,
        symbol=symbol,
        candle_hours=candle_hours,
    )
    stats = metrics_module.compute(result, candle_hours=candle_hours)
    print_report(symbol, timeframe, result, stats)
    return result, stats


def print_report(symbol, timeframe, result, stats):
    print()
    print(f"========== AGMCIS 回測 | {symbol} {timeframe} ==========")
    print(f"K 棒 {result.bars} 根   起始資金 {stats.start_balance:.2f}"
          f"   期末資金 {stats.end_balance:.2f}")
    print("-" * 52)

    for line in stats.summary_lines():
        print(line)

    print("-" * 52)
    print(f"出場原因     {stats.exit_reasons or '無'}")
    print(f"訊號被拒     {result.skipped_signals} 次"
          f"   持倉中略過 {result.signals_while_in_position} 根")

    if stats.warnings:
        print("-" * 52)
        for warning in stats.warnings:
            print(f"⚠️  {warning}")

    print("=" * 52)


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTC/USDT"
    timeframe = sys.argv[2] if len(sys.argv) > 2 else "1h"
    run_backtest(symbol, timeframe)
