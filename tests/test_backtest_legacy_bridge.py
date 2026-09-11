"""
舊策略模組接到新回測引擎的橋接層。

重點不是「策略賺不賺」,而是**舊的偏誤不能透過這個橋接層溜回來**:
訊號一樣在下一根開盤成交、一定有停損、成本一定算進去。
"""
import math
import sys
import os
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from agmcis.backtest import legacy
from agmcis.backtest.costs import DEFAULT_COSTS, ZERO_COSTS


def make_frame(count=200, start=100.0, step=0.5):
    """緩升的 K 棒,足夠讓 EMA / MACD / ADX 暖機。"""
    rows = []
    price = start
    for i in range(count):
        open_ = price
        close = price + step
        rows.append({
            "timestamp": i * 3_600_000,
            "open": open_,
            "high": max(open_, close) + 0.2,
            "low": min(open_, close) - 0.2,
            "close": close,
            "volume": 1000.0 + i,
        })
        price = close
    return legacy.add_indicators(pd.DataFrame(rows))


def wavy_frame(count=400, base=100.0):
    """
    有漲有跌的價格序列。

    單調上漲的測試資料會讓 RSI 永遠是 100、ADX 永遠是 100,
    真實策略模組一筆都不會進場 —— 那樣的測試是通過了但什麼都沒驗到。
    """
    rows = []
    for i in range(count):
        trend = i * 0.15
        wave = 12.0 * math.sin(i / 11.0) + 4.0 * math.sin(i / 3.0)
        close = base + trend + wave
        open_ = base + (i - 1) * 0.15 + 12.0 * math.sin((i - 1) / 11.0) \
            + 4.0 * math.sin((i - 1) / 3.0)
        rows.append({
            "timestamp": i * 3_600_000,
            "open": open_,
            "high": max(open_, close) + 0.8,
            "low": min(open_, close) - 0.8,
            "close": close,
            "volume": 1000.0 + 400.0 * abs(math.sin(i / 5.0)),
        })
    return legacy.add_indicators(pd.DataFrame(rows))


def always_buy_module(sell=False):
    module = types.ModuleType("always_buy")
    module.buy_signal = lambda *args: True
    module.sell_signal = lambda *args: sell
    return module


class TestIndicators(unittest.TestCase):

    def test_add_indicators_provides_every_argument_the_old_modules_expect(self):
        df = make_frame()
        for name in legacy.STRATEGY_ARG_ORDER:
            self.assertIn(name, df.columns, f"缺少 {name}")

    def test_warmup_rows_are_nan_and_are_not_traded(self):
        """指標還沒暖機完就進場,等於用不存在的資訊做決定。"""
        df = make_frame()
        signal_fn, _ = legacy.build_signal_fns(df, always_buy_module())

        self.assertIsNone(signal_fn([], 0))


class TestStopLossIsAlwaysPresent(unittest.TestCase):
    """
    舊的 run_strategy() 完全沒有停損,虧損沒有上限。
    新引擎會拒絕沒有停損的訊號,所以橋接層必須自己補上。
    """

    def test_every_signal_carries_a_stop_below_the_entry(self):
        df = make_frame()
        signal_fn, _ = legacy.build_signal_fns(df, always_buy_module())

        checked = 0
        for index in range(legacy.WARMUP_BARS, len(df)):
            signal = signal_fn(None, index)
            if signal is None:
                continue
            checked += 1
            self.assertIn("stop_loss", signal)
            self.assertLess(signal["stop_loss"], float(df["close"].iloc[index]))

        self.assertGreater(checked, 0, "測試資料應該至少產生一個訊號")

    def test_a_wider_atr_multiple_puts_the_stop_further_away(self):
        df = make_frame()
        index = len(df) - 1

        near, _ = legacy.build_signal_fns(df, always_buy_module(), atr_stop_multiple=1.0)
        far, _ = legacy.build_signal_fns(df, always_buy_module(), atr_stop_multiple=3.0)

        self.assertLess(far(None, index)["stop_loss"], near(None, index)["stop_loss"])


class TestNoLookAheadThroughTheBridge(unittest.TestCase):

    def test_entry_price_is_the_next_candle_open(self):
        df = make_frame()
        result = legacy.run_strategy_detailed(
            df, always_buy_module(), costs=ZERO_COSTS,
        )

        self.assertTrue(result.trades)
        trade = result.trades[0]
        expected_open = float(df["open"].iloc[trade.entry_index])
        self.assertAlmostEqual(trade.entry_price, expected_open, places=6)

    def test_strategy_exit_also_fills_on_the_next_candle_open(self):
        """
        sell_signal 在第 i 根收盤成立,成交必須在第 i+1 根的開盤價。
        舊版用第 i 根的收盤價出場 —— 那是偷看未來。
        """
        df = make_frame()
        result = legacy.run_strategy_detailed(
            df, always_buy_module(sell=True), costs=ZERO_COSTS,
        )

        exits = [t for t in result.trades if t.exit_reason == "策略出場"]
        self.assertTrue(exits, "應該要有策略出場的交易")

        trade = exits[0]
        expected_open = float(df["open"].iloc[trade.exit_index])
        self.assertAlmostEqual(trade.exit_price, expected_open, places=6)
        self.assertGreater(trade.exit_index, trade.entry_index)


class TestCostsSurviveTheBridge(unittest.TestCase):

    def test_default_costs_make_the_result_worse_than_zero_costs(self):
        df = make_frame()
        free = legacy.run_strategy(df, always_buy_module(), costs=ZERO_COSTS)
        charged = legacy.run_strategy(df, always_buy_module(), costs=DEFAULT_COSTS)

        self.assertLess(charged, free)

    def test_run_strategy_still_returns_a_number_for_the_old_callers(self):
        """strategy_lab.py / strategy_optimizer.py 仍然 capital = run_strategy(...)。"""
        df = make_frame()
        capital = legacy.run_strategy(df, always_buy_module())

        self.assertIsInstance(capital, float)
        self.assertTrue(math.isfinite(capital))


class TestPositionSizingIsNotAllIn(unittest.TestCase):
    """舊版每筆押上 100% 資金完全複利,報酬率被指數級放大。"""

    def test_margin_is_a_small_fraction_of_the_balance(self):
        df = make_frame()
        result = legacy.run_strategy_detailed(
            df, always_buy_module(), start_balance=10000.0,
            risk_per_trade_pct=1.0, costs=ZERO_COSTS,
        )

        self.assertTrue(result.trades)
        for trade in result.trades:
            self.assertLess(trade.size_usdt, 10000.0)


class TestOldModuleContract(unittest.TestCase):

    def test_a_module_without_buy_signal_is_rejected_loudly(self):
        """靜默失敗是被禁止的 —— 壞掉的策略模組不能安靜地回傳 0 筆交易。"""
        broken = types.ModuleType("broken")

        with self.assertRaises(AttributeError):
            legacy.build_signal_fns(make_frame(), broken)

    def test_a_module_without_sell_signal_still_runs(self):
        """沒有 sell_signal 的策略靠停損停利出場,不該直接爆掉。"""
        module = types.ModuleType("buy_only")
        module.buy_signal = lambda *args: True

        result = legacy.run_strategy_detailed(make_frame(), module, costs=ZERO_COSTS)
        self.assertIsNotNone(result)

    def test_real_strategy_modules_actually_produce_trades(self):
        """
        三個實際的策略模組都要能跑完並且**真的開出倉位**。

        只斷言「不拋例外」是不夠的 —— 參數順序接錯時每個 buy_signal
        都會安靜地回 False,回測回報 0 筆交易,測試照樣通過。
        一筆都不交易的管線跟會崩潰的管線一樣是壞的。
        """
        import strategies.breakout_strategy as breakout_strategy
        import strategies.ema_strategy as ema_strategy
        import strategies.rsi_strategy as rsi_strategy

        df = wavy_frame()
        for module in (ema_strategy, rsi_strategy, breakout_strategy):
            with self.subTest(module=module.__name__):
                result = legacy.run_strategy_detailed(df, module, costs=ZERO_COSTS)
                self.assertTrue(
                    result.trades,
                    f"{module.__name__} 在有漲有跌的資料上一筆都沒交易,"
                    f"參數可能接錯了",
                )

    def test_the_argument_order_matches_what_the_modules_expect(self):
        """
        參數順序錯了不會拋例外,只會安靜地算出錯的訊號。
        這裡用一個只在 rsi 位置成立的假模組把順序釘死。
        """
        seen = {}

        module = types.ModuleType("order_probe")

        def buy_signal(price, ema20, ema50, rsi, macd, macd_signal,
                       volume, vol_ma, atr, adx):
            seen.update(price=price, ema20=ema20, ema50=ema50, rsi=rsi,
                        atr=atr, adx=adx, volume=volume)
            return False

        module.buy_signal = buy_signal

        df = wavy_frame()
        signal_fn, _ = legacy.build_signal_fns(df, module)
        index = len(df) - 1
        signal_fn(None, index)

        row = df.iloc[index]
        self.assertAlmostEqual(seen["price"], float(row["close"]), places=6)
        self.assertAlmostEqual(seen["ema20"], float(row["ema20"]), places=6)
        self.assertAlmostEqual(seen["rsi"], float(row["rsi"]), places=6)
        self.assertAlmostEqual(seen["atr"], float(row["atr"]), places=6)
        self.assertAlmostEqual(seen["adx"], float(row["adx"]), places=6)
        self.assertAlmostEqual(seen["volume"], float(row["volume"]), places=6)


if __name__ == "__main__":
    unittest.main()
