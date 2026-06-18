import ccxt
import pandas as pd
import numpy as np

from ta.trend import EMAIndicator, MACD
from ta.momentum import RSIIndicator

exchange = ccxt.binance()


START_CAPITAL = 10000
RISK_PER_TRADE = 0.02
TAKE_PROFIT = 0.04
STOP_LOSS = 0.02
FEE = 0.001


def get_data(symbol="BTC/USDT", timeframe="1h", limit=1500):

    data = exchange.fetch_ohlcv(
        symbol,
        timeframe=timeframe,
        limit=limit
    )

    df = pd.DataFrame(
        data,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
    )

    return df


def run_backtest():

    df = get_data()

    close = df["close"]

    df["ema20"] = EMAIndicator(
        close=close,
        window=20
    ).ema_indicator()

    df["ema50"] = EMAIndicator(
        close=close,
        window=50
    ).ema_indicator()

    df["rsi"] = RSIIndicator(
        close=close,
        window=14
    ).rsi()

    macd = MACD(close=close)

    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()

    capital = START_CAPITAL

    equity_curve = [capital]

    trades = []

    in_position = False

    entry_price = 0

    for i in range(60, len(df)):

        price = float(df["close"].iloc[i])

        ema20 = float(df["ema20"].iloc[i])
        ema50 = float(df["ema50"].iloc[i])

        rsi = float(df["rsi"].iloc[i])

        macd_now = float(df["macd"].iloc[i])
        macd_sig = float(df["macd_signal"].iloc[i])

        buy_signal = (
            price > ema20
            and ema20 > ema50
            and 45 <= rsi <= 70
            and macd_now > macd_sig
        )

        if not in_position and buy_signal:

            in_position = True

            entry_price = price

            stop_price = entry_price * (1 - STOP_LOSS)

            take_price = entry_price * (1 + TAKE_PROFIT)

        elif in_position:

            exit_trade = False

            if price <= stop_price:
                exit_trade = True

            elif price >= take_price:
                exit_trade = True

            elif macd_now < macd_sig:
                exit_trade = True

            if exit_trade:

                pnl_pct = (
                    (price - entry_price)
                    / entry_price
                )

                pnl_pct -= FEE * 2

                capital *= (1 + pnl_pct)

                trades.append(pnl_pct * 100)

                equity_curve.append(capital)

                in_position = False

    total_trades = len(trades)

    wins = len([t for t in trades if t > 0])

    losses = len([t for t in trades if t <= 0])

    win_rate = (
        wins / total_trades * 100
        if total_trades > 0
        else 0
    )

    total_return = (
        (capital - START_CAPITAL)
        / START_CAPITAL
        * 100
    )

    peak = equity_curve[0]

    max_drawdown = 0

    for value in equity_curve:

        if value > peak:
            peak = value

        dd = (peak - value) / peak * 100

        if dd > max_drawdown:
            max_drawdown = dd

    gross_profit = sum(
        [t for t in trades if t > 0]
    )

    gross_loss = abs(
        sum([t for t in trades if t < 0])
    )

    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0
        else 0
    )

    print()
    print("========== AGMCIS V8.5 ==========")
    print(f"初始資金：{START_CAPITAL}")
    print(f"最終資金：{capital:.2f}")
    print("--------------------------------")
    print(f"總交易數：{total_trades}")
    print(f"獲利筆數：{wins}")
    print(f"虧損筆數：{losses}")
    print(f"勝率：{win_rate:.2f}%")
    print("--------------------------------")
    print(f"總報酬率：{total_return:.2f}%")
    print(f"最大回撤：{max_drawdown:.2f}%")
    print(f"Profit Factor：{profit_factor:.2f}")
    print("================================")


run_backtest()