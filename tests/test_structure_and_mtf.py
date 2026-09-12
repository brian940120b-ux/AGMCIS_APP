"""
市場結構、多時間框架階層、市況擴充(Master Prompt 第二十三 / 二十四 / 二十五節)。

三個主題,一個共同的原則:**資料不足時回「不知道」,不回一個看起來
合理的數字。** 一個用 5 根 K 棒算出來的「支撐位」不是支撐位,
它只是那 5 根裡的最低價。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.analysis import mtf, structure
from agmcis.analysis.regime import (
    PANIC_ATR_PCT,
    MarketRegime,
    Regime,
    RiskAppetite,
    Volatility,
    classify_risk_appetite,
    detect,
)
from agmcis.analysis.indicators import Indicators
from agmcis.core.enums import Direction


def bars(highs, lows, closes=None, volumes=None):
    closes = closes or [(h + l) / 2 for h, l in zip(highs, lows)]
    volumes = volumes or [100.0] * len(highs)
    return [
        {"time": i, "open": closes[i], "high": highs[i],
         "low": lows[i], "close": closes[i], "volume": volumes[i]}
        for i in range(len(highs))
    ]


def rising(n=40, step=1.0):
    """穩定上升、帶有回檔的序列,會產生 HH / HL。"""
    highs, lows = [], []
    base = 100.0
    for i in range(n):
        wave = 2.0 if i % 4 in (1, 2) else 0.0
        highs.append(base + i * step + wave)
        lows.append(base + i * step - 2.0 + wave)
    return bars(highs, lows)


def falling(n=40, step=1.0):
    rows = rising(n, step)
    top = max(r["high"] for r in rows)
    return bars(
        [top - (r["low"] - 100.0) for r in rows],
        [top - (r["high"] - 100.0) for r in rows],
    )


class TestSwingPoints(unittest.TestCase):

    def test_the_last_bars_are_never_swing_points(self):
        """
        它們右邊的 K 棒還沒發生。允許最後一根成為擺動點,
        等於用未來資料判斷現在 —— 那正是第三十四節禁止的事。
        """
        rows = rising(40)
        points = structure.swing_points(rows, strength=2)

        self.assertTrue(points)
        self.assertLessEqual(max(p.index for p in points), len(rows) - 3)

    def test_too_few_candles_gives_nothing(self):
        self.assertEqual(structure.swing_points(bars([1, 2], [0, 1])), [])

    def test_malformed_candles_give_nothing_rather_than_crashing(self):
        self.assertEqual(structure.swing_points([{"high": 1}] * 30), [])


class TestStructure(unittest.TestCase):

    def test_a_rising_series_is_an_uptrend(self):
        result = structure.analyse(rising(40))

        self.assertEqual(result.trend, structure.UPTREND)
        self.assertTrue(result.higher_high)
        self.assertTrue(result.higher_low)

    def test_a_falling_series_is_a_downtrend(self):
        result = structure.analyse(falling(40))

        self.assertEqual(result.trend, structure.DOWNTREND)
        self.assertTrue(result.lower_high)
        self.assertTrue(result.lower_low)

    def test_too_few_candles_gives_unknown_not_range(self):
        """
        「資料不夠」與「盤整」是兩件事。混在一起會讓系統在
        剛上市的標的上以為自己看到了盤整。
        """
        result = structure.analyse(rising(10))

        self.assertEqual(result.trend, structure.UNKNOWN)
        self.assertIn("不做結構判斷", result.detail)

    def test_support_is_the_latest_swing_low_not_the_period_low(self):
        """
        整段的最低價通常在很久以前,對現在的價格沒有意義。
        """
        result = structure.analyse(rising(40))
        period_low = min(r["low"] for r in rising(40))

        self.assertIsNotNone(result.support)
        self.assertGreater(result.support, period_low)

    def test_not_enough_swings_says_so(self):
        flat = bars([100.0] * 30, [99.0] * 30)
        result = structure.analyse(flat)

        self.assertEqual(result.trend, structure.UNKNOWN)
        self.assertIn("擺動點不足", result.detail)


class TestLiquiditySweep(unittest.TestCase):
    """
    「穿過又收回來」與「穿過並站穩」是完全不同的兩件事。
    """

    @staticmethod
    def _last_swing_high(rows):
        """
        參考的是**最後一個擺動高點**,不是整段的最高價。
        整段最高價通常在很久以前,拿它當基準測不到真正的行為。
        """
        points = [
            p for p in structure.swing_points(rows) if p.kind == "high"
        ]
        return points[-1].price

    def _with_sweep(self, reclaim):
        rows = rising(40)
        level = self._last_swing_high(rows)
        rows.append({
            "time": 999,
            "open": level,
            "high": level * 1.02,
            "low": level * 0.99,
            "close": level * (0.99 if reclaim else 1.015),
            "volume": 100.0,
        })
        return rows

    def test_a_false_breakout_is_marked_reclaimed(self):
        sweep = structure.liquidity_sweep(self._with_sweep(reclaim=True))

        self.assertTrue(sweep.swept)
        self.assertTrue(sweep.reclaimed)
        self.assertEqual(sweep.direction, "high")

    def test_holding_above_is_a_breakout_not_a_sweep(self):
        """
        把兩者都算成掃蕩的話,任何一段乾淨的上升趨勢裡每一根都會
        回報掃蕩 —— 因為每一根都在創新高。那個訊號沒有資訊量。
        """
        sweep = structure.liquidity_sweep(self._with_sweep(reclaim=False))

        self.assertFalse(sweep.swept)
        self.assertTrue(sweep.broke_out)
        self.assertTrue(sweep.penetrated)

    def test_a_clean_uptrend_does_not_report_sweeps_every_bar(self):
        sweep = structure.liquidity_sweep(rising(40))
        self.assertFalse(sweep.swept)

    def test_a_tick_of_noise_is_not_a_penetration(self):
        """穿過一個 tick 是浮點誤差或雜訊,不是穿越。"""
        rows = rising(40)
        level = self._last_swing_high(rows)
        rows.append({
            "time": 999, "open": level,
            "high": level * 1.0001,      # 遠低於 0.05% 的門檻
            "low": level * 0.9999,
            "close": level, "volume": 100.0,
        })

        self.assertFalse(structure.liquidity_sweep(rows).penetrated)


class TestVwap(unittest.TestCase):

    def test_volume_weights_the_price(self):
        rows = bars([10, 10], [10, 10], closes=[10, 20], volumes=[1, 99])
        # 幾乎所有量都在第二根,VWAP 應該非常靠近它
        value = structure.vwap(rows)

        self.assertIsNotNone(value)
        self.assertGreater(value, 9.0)

    def test_no_volume_gives_none_not_a_simple_average(self):
        """
        退化成簡單平均會產生一個名字叫 VWAP 但其實是 SMA 的數字,
        而讀的人不會知道。
        """
        rows = bars([10] * 5, [9] * 5, volumes=[0] * 5)
        self.assertIsNone(structure.vwap(rows))

    def test_no_candles_gives_none(self):
        self.assertIsNone(structure.vwap([]))


class TestVolumeProfile(unittest.TestCase):

    def test_the_poc_lands_where_the_volume_is(self):
        highs = [100.0] * 20 + [200.0] * 5
        lows = [99.0] * 20 + [199.0] * 5
        volumes = [1000.0] * 20 + [1.0] * 5

        profile = structure.volume_profile(bars(highs, lows, volumes=volumes))

        self.assertIsNotNone(profile.poc)
        self.assertLess(profile.poc, 150.0)

    def test_the_value_area_brackets_the_poc(self):
        profile = structure.volume_profile(rising(60))

        self.assertLessEqual(profile.value_area_low, profile.poc)
        self.assertGreaterEqual(profile.value_area_high, profile.poc)

    def test_no_volume_gives_no_profile(self):
        rows = bars([10] * 30, [9] * 30, volumes=[0] * 30)
        profile = structure.volume_profile(rows)

        self.assertIsNone(profile.poc)
        self.assertIn("沒有成交量", profile.detail)

    def test_too_few_candles_gives_no_profile(self):
        self.assertIsNone(structure.volume_profile(rising(10)).poc)


class TestTheMtfHierarchyIsNotAVote(unittest.TestCase):
    """
    投票允許低時間框架蓋過高時間框架。15 分鐘線看多、日線看空,
    加權之後可能得到「偏多」—— 但那是下降趨勢裡的一個反彈,
    而那正是最容易虧錢的一種 setup。
    """

    def test_the_high_timeframes_decide_the_direction(self):
        view = mtf.analyse({
            "1d": "做多", "4h": "做多", "1h": "做多",
            "15m": "做多", "5m": "做多",
        })

        self.assertIs(view.direction, Direction.LONG)
        self.assertTrue(view.aligned)

    def test_low_timeframes_cannot_flip_the_direction(self):
        view = mtf.analyse({
            "1d": "做多", "4h": "做多", "1h": "做多",
            "15m": "做空", "5m": "做空",
        })

        self.assertIs(view.direction, Direction.LONG)
        self.assertTrue(view.is_actionable)
        self.assertEqual(view.timing_score, 0.0)

    def test_low_timeframes_only_change_the_timing_score(self):
        good = mtf.analyse({
            "1d": "做多", "4h": "做多", "1h": "做多",
            "15m": "做多", "5m": "做多",
        })
        bad = mtf.analyse({
            "1d": "做多", "4h": "做多", "1h": "做多",
            "15m": "做空", "5m": "做空",
        })

        self.assertIs(good.direction, bad.direction)
        self.assertGreater(good.timing_score, bad.timing_score)

    def test_conflicting_high_timeframes_block_the_trade(self):
        view = mtf.analyse({"1d": "做多", "4h": "做空", "1h": "做多"})

        self.assertFalse(view.is_actionable)
        self.assertEqual(view.blocked_reason, "高時間框架互相矛盾")

    def test_a_conflicting_structure_downgrades_to_wait(self):
        """1H 與高時間框架相反 = 趨勢裡的反向段落,不是進場點。"""
        view = mtf.analyse({
            "1d": "做多", "4h": "做多", "1h": "做空",
            "15m": "做多", "5m": "做多",
        })

        self.assertFalse(view.is_actionable)
        self.assertIs(view.direction, Direction.WAIT)

    def test_missing_high_timeframe_data_blocks_rather_than_neutralises(self):
        """
        「日線資料拿不到」與「日線是中性的」是完全不同的兩件事。
        把前者當成後者,系統會在最沒有資訊的時候最敢交易。
        """
        view = mtf.analyse({"4h": "做多", "1h": "做多"})

        self.assertFalse(view.is_actionable)
        self.assertEqual(view.blocked_reason, "高時間框架資料不足")

    def test_missing_low_timeframes_do_not_block(self):
        view = mtf.analyse({"1d": "做多", "4h": "做多", "1h": "做多"})

        self.assertTrue(view.is_actionable)

    def test_indicators_objects_are_accepted(self):
        bullish = Indicators(
            symbol="BTC/USDT", timeframe="1d", ema20=110.0, ema50=100.0,
        )
        view = mtf.analyse({"1d": bullish, "4h": bullish, "1h": bullish})

        self.assertIs(view.direction, Direction.LONG)

    def test_a_blocked_view_scores_zero_not_fifty(self):
        """被高時間框架矛盾擋下來的訊號,多時間框架分數就是 0。"""
        self.assertEqual(mtf.score({"1d": "做多", "4h": "做空"}), 0.0)

    def test_every_timeframe_has_a_role(self):
        view = mtf.analyse({})
        roles = {v.timeframe: v.role for v in view.views}

        self.assertEqual(roles["1d"], "總體趨勢")
        self.assertEqual(roles["5m"], "進場時機")


class TestPanicAndRiskAppetite(unittest.TestCase):

    def _indicators(self, bullish=True, atr_pct=1.0, adx=30.0, price=100.0):
        return Indicators(
            symbol="X/USDT", timeframe="1h", price=price,
            ema20=110.0 if bullish else 90.0, ema50=100.0,
            rsi=50.0, macd=1.0, macd_signal=0.5, adx=adx,
            atr=price * atr_pct / 100.0,
        )

    def test_extreme_volatility_plus_downtrend_is_panic(self):
        regime = detect(self._indicators(bullish=False, atr_pct=PANIC_ATR_PCT + 1))

        self.assertIs(regime.regime, Regime.PANIC)

    def test_extreme_volatility_upwards_is_not_panic(self):
        """暴漲的波動一樣極端,但那是另一回事。"""
        regime = detect(self._indicators(bullish=True, atr_pct=PANIC_ATR_PCT + 1))

        self.assertIsNot(regime.regime, Regime.PANIC)

    def test_a_normal_downtrend_is_not_panic(self):
        regime = detect(self._indicators(bullish=False, atr_pct=2.0))

        self.assertIn(regime.regime, (Regime.BEAR, Regime.STRONG_BEAR))

    def test_panic_is_not_tradeable(self):
        """
        順勢做空在 STRONG_BEAR 合理,在 PANIC 不合理 ——
        停損會被掃、點差會擴大、反彈的幅度與速度完全不同。
        """
        self.assertFalse(
            MarketRegime(regime=Regime.PANIC,
                         volatility=Volatility.HIGH).is_tradeable
        )

    def test_risk_appetite_is_a_separate_dimension(self):
        """
        一個標的可以在 BULL 市況但市場整體 RISK_OFF。
        塞進同一個列舉就得二選一,而正確答案是「都是」。
        """
        regime = detect(self._indicators(bullish=True))

        self.assertIs(regime.regime, Regime.BULL)
        self.assertIsInstance(regime.risk_appetite, RiskAppetite)

    def test_both_bullish_is_risk_on(self):
        btc = self._indicators(bullish=True)
        own = self._indicators(bullish=True)

        self.assertIs(classify_risk_appetite(own, btc), RiskAppetite.RISK_ON)

    def test_both_bearish_is_risk_off(self):
        btc = self._indicators(bullish=False)
        own = self._indicators(bullish=False)

        self.assertIs(classify_risk_appetite(own, btc), RiskAppetite.RISK_OFF)

    def test_no_benchmark_gives_unknown_not_neutral(self):
        """
        沒有基準就沒有「相對」可言。回 NEUTRAL 會假裝我們知道
        一件其實不知道的事。
        """
        self.assertIs(
            classify_risk_appetite(self._indicators(), None),
            RiskAppetite.UNKNOWN,
        )


if __name__ == "__main__":
    unittest.main()
