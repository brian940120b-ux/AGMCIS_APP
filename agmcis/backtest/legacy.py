"""
把 strategies/ 底下的舊策略模組接到新的回測引擎上。

舊的 backtest_engine.run_strategy() 有四個致命問題:

  1. **用第 i 根的收盤價產生訊號,又用同一根的收盤價成交。** 偷看未來。
  2. **完全沒有停損。** 只靠 sell_signal 出場,虧損沒有上限。
  3. **沒有成本、沒有槓桿、沒有強平。**
  4. **每筆押上 100% 資金完全複利。** 報酬率會被指數級放大。

這個橋接層讓舊策略模組**原封不動**跑在新引擎上:
買賣訊號還是那些函式決定,但進出場時點、部位大小、成本與強平
全部交給 BacktestEngine —— 也就是改掉的是回測方法,不是策略本身。

停損:舊策略模組沒有停損概念,這裡用 ATR 補上(預設 2 × ATR)。
      沒有停損的部位在新引擎裡會直接被拒絕,這是 Phase 5 定下的鐵律。
"""
import logging

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, EMAIndicator, MACD
from ta.volatility import AverageTrueRange

from agmcis.backtest.costs import DEFAULT_COSTS
from agmcis.backtest.engine import BacktestEngine
from agmcis.data import market_data

logger = logging.getLogger("agmcis.backtest.legacy")

WARMUP_BARS = 60
DEFAULT_ATR_STOP_MULTIPLE = 2.0

# 舊策略模組的參數順序。改動這個順序會讓每個策略都收到錯的值,
# 所以集中在這裡定義一次。
STRATEGY_ARG_ORDER = (
    "close", "ema20", "ema50", "rsi", "macd",
    "macd_signal", "volume", "vol_ma20", "atr", "adx",
)


def add_indicators(df):
    """補上舊策略模組需要的技術指標。回傳同一個 DataFrame。"""
    close = df["close"]

    df["ema20"] = EMAIndicator(close=close, window=20).ema_indicator()
    df["ema50"] = EMAIndicator(close=close, window=50).ema_indicator()
    df["rsi"] = RSIIndicator(close=close, window=14).rsi()

    macd = MACD(close=close)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()

    df["adx"] = ADXIndicator(
        high=df["high"], low=df["low"], close=close, window=14,
    ).adx()

    df["atr"] = AverageTrueRange(
        high=df["high"], low=df["low"], close=close, window=14,
    ).average_true_range()

    df["vol_ma20"] = df["volume"].rolling(20).mean()

    return df


def load_data(symbol, timeframe="1h", limit=1500):
    """
    取得帶指標的 K 棒。

    資料來源是 **BingX**,不是 Binance。
    舊版用 ccxt.binance() 抓資料回測,但實際下單在 BingX ——
    價格、流動性、資金費率都不一樣,回測結果對不上真實成交環境。
    """
    df, report = market_data.get_ohlcv_checked(
        symbol, timeframe=timeframe, limit=limit, min_candles=WARMUP_BARS + 20,
    )

    if df is None:
        raise ValueError(f"{symbol} {timeframe} 資料品質不合格,不回測:{report.summary}")

    return add_indicators(df)


def _row_args(row):
    """把一列 DataFrame 轉成舊策略模組要的位置參數。"""
    return [float(row[name]) for name in STRATEGY_ARG_ORDER]


def _has_nan(values):
    return any(value != value for value in values)   # NaN != NaN


def build_signal_fns(df, strategy_module, atr_stop_multiple=DEFAULT_ATR_STOP_MULTIPLE):
    """
    回傳 (signal_fn, exit_fn),兩個都只看得到當下這一根為止的資料。

    引擎會保證傳進來的 history 不含未來,這裡再用 index 去 df 取指標 ——
    indicator 是用整段資料算的,但第 i 列的值只依賴第 i 根以前的價格,
    所以不構成前視偏誤。
    """
    buy_signal = getattr(strategy_module, "buy_signal", None)
    sell_signal = getattr(strategy_module, "sell_signal", None)

    if buy_signal is None:
        raise AttributeError(f"{strategy_module.__name__} 沒有 buy_signal()")

    def signal_fn(history, index):
        if index >= len(df):
            return None

        args = _row_args(df.iloc[index])
        if _has_nan(args):
            return None      # 指標還沒暖機完

        if not buy_signal(*args):
            return None

        close = args[0]
        atr = args[STRATEGY_ARG_ORDER.index("atr")]
        stop = close - atr_stop_multiple * atr

        # ATR 太小或太大都會讓停損失去意義
        if atr <= 0 or stop <= 0 or stop >= close:
            return None

        return {"direction": "做多", "stop_loss": stop}

    def exit_fn(history, index, position):
        if sell_signal is None or index >= len(df):
            return False

        args = _row_args(df.iloc[index])
        if _has_nan(args):
            return False

        return bool(sell_signal(*args))

    return signal_fn, exit_fn


def run_strategy_detailed(df, strategy_module, start_balance=10000.0,
                          costs=None, risk_per_trade_pct=1.0,
                          max_leverage=3.0, symbol="BACKTEST",
                          atr_stop_multiple=DEFAULT_ATR_STOP_MULTIPLE,
                          candle_hours=1.0):
    """跑完整回測,回傳 BacktestResult。"""
    signal_fn, exit_fn = build_signal_fns(df, strategy_module, atr_stop_multiple)

    candles = df.to_dict("records")
    engine = BacktestEngine(
        costs=costs if costs is not None else DEFAULT_COSTS,
        start_balance=start_balance,
        risk_per_trade_pct=risk_per_trade_pct,
        max_leverage=max_leverage,
        candle_hours=candle_hours,
    )

    return engine.run(candles, signal_fn, symbol=symbol,
                      warmup=WARMUP_BARS, exit_fn=exit_fn)


def run_strategy(df, strategy_module, **kwargs):
    """
    維持舊簽名:回傳期末資金。

    數字本身會比舊版難看很多 —— 因為現在有手續費、滑點、資金費用、
    停損與固定風險部位,而不是每筆押上全部資金。那才是真實的樣子。
    """
    return run_strategy_detailed(df, strategy_module, **kwargs).end_balance
