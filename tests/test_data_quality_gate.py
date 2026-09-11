"""
資料品質 gate。

原本 technical_service 是 `except Exception: pass`,指標算失敗回傳全 None 與
trend="UNKNOWN",而 calculate_scanner_confidence 遇到 None 會給出 50 分的「中性」分數 ——
系統無法區分「市場中性」與「資料壞掉」,可能在壞資料上開倉。
"""
import unittest
from unittest.mock import patch

import direction_engine
import scanner_service
from agmcis.core.enums import Direction
import technical_service
from agmcis.data import quality


class TestIndicatorFailuresAreVisible(unittest.TestCase):
    """
    Phase 2 起,K 棒品質在**算指標之前**就被檢查(get_ohlcv_checked),
    所以這裡 patch 的是那道 gate。行為要求不變:失敗必須被記錄,
    而且回傳 data_ok=False,絕不悄悄變成中性訊號。
    """

    def _report(self, code, detail):
        report = quality.QualityReport(symbol="BTC/USDT", timeframe="1h")
        report.add(code, quality.SEVERITY_ERROR, detail)
        return report

    def test_fetch_failure_marks_data_not_ok(self):
        report = self._report("FETCH_FAILED", "api down")

        with patch.object(technical_service, "get_price", return_value=100.0), \
             patch.object(technical_service, "get_ohlcv_checked", return_value=(None, report)), \
             patch.object(technical_service, "logger") as log:
            result = technical_service.get_indicators("BTC/USDT")

        self.assertFalse(result["data_ok"])
        self.assertIn("FETCH_FAILED", result["data_error"])
        self.assertTrue(log.warning.called, "失敗必須被記錄,不能靜默")

    def test_insufficient_candles_marks_data_not_ok(self):
        report = self._report("INSUFFICIENT_CANDLES", "只有 10 根,需要至少 60 根")

        with patch.object(technical_service, "get_price", return_value=100.0), \
             patch.object(technical_service, "get_ohlcv_checked", return_value=(None, report)), \
             patch.object(technical_service, "logger"):
            result = technical_service.get_indicators("BTC/USDT")

        self.assertFalse(result["data_ok"])
        self.assertIn("INSUFFICIENT_CANDLES", result["data_error"])

    def test_stale_data_marks_data_not_ok(self):
        report = self._report("STALE_DATA", "最後一根 K 棒已經是 240 分鐘前")

        with patch.object(technical_service, "get_price", return_value=100.0), \
             patch.object(technical_service, "get_ohlcv_checked", return_value=(None, report)), \
             patch.object(technical_service, "logger"):
            result = technical_service.get_indicators("BTC/USDT")

        self.assertFalse(result["data_ok"])
        self.assertIn("STALE_DATA", result["data_error"])
        self.assertTrue(result["data_issues"])

    def test_indicator_calculation_failure_is_logged(self):
        """K 棒過了品質檢查,但指標算出 NaN 時仍必須被擋下。"""
        import pandas as pd

        flat = pd.DataFrame({
            "timestamp": range(150), "open": [1.0] * 150, "high": [1.0] * 150,
            "low": [1.0] * 150, "close": [1.0] * 150, "volume": [1.0] * 150,
        })
        report = quality.QualityReport(symbol="BTC/USDT", timeframe="1h")

        with patch.object(technical_service, "get_price", return_value=1.0), \
             patch.object(technical_service, "get_ohlcv_checked", return_value=(flat, report)), \
             patch.object(technical_service, "RSIIndicator", side_effect=RuntimeError("boom")), \
             patch.object(technical_service, "logger") as log:
            result = technical_service.get_indicators("BTC/USDT")

        self.assertFalse(result["data_ok"])
        self.assertIn("indicator_calc_failed", result["data_error"])
        self.assertTrue(log.exception.called)


class TestBadDataNeverBecomesASignal(unittest.TestCase):
    """
    Phase 6 之後訊號由統一管線產生,所以這裡驗的是管線的行為。
    要求不變:資料壞掉絕不可以被當成中性訊號。
    """

    BAD = {"data_ok": False, "data_error": "ohlcv_fetch_failed: boom",
           "trend": "UNKNOWN", "rsi": None, "macd": None, "macd_signal": None,
           "ema20": None, "ema60": None, "atr": None, "price": 100.0}

    def test_direction_engine_returns_wait(self):
        self.assertEqual(direction_engine.get_trade_direction(self.BAD), "WAIT")

    def test_position_evaluation_returns_no_data_when_agents_cannot_see(self):
        """
        Phase 9:持倉評估改由 Agent 群產生。所有 Agent 棄權(通常就是資料不可用)
        時必須回 No Data,不能給出一個看起來很正常的建議。
        原本這條測的是 decision_engine,那個模組是第四套獨立評分,已移除。
        """
        import ai_decision_service
        from agmcis.agents.base import AgentOpinion
        from agmcis.agents.consensus import Deliberation

        deliberation = Deliberation(
            symbol="BTC/USDT",
            opinions=[AgentOpinion(agent=f"a{i}") for i in range(3)],
        )

        with patch.object(ai_decision_service.agent_pipeline, "analyse_symbol",
                          return_value=(deliberation, None)):
            result = ai_decision_service._evaluate_position(
                {"symbol": "BTC/USDT", "signal": "做多"},
            )

        self.assertEqual(result["trade_signal"], "⚪ No Data")
        self.assertIsNone(result["confidence"])

    def test_pipeline_emits_a_wait_signal_when_data_is_bad(self):
        """資料品質不合格時,管線回傳帶原因的 WAIT Signal,而不是 None 或例外。"""
        from agmcis.data import quality
        from agmcis.signal import pipeline

        report = quality.QualityReport(symbol="BTC/USDT", timeframe="1h")
        report.add("STALE_DATA", quality.SEVERITY_ERROR, "資料過期")

        with patch("agmcis.data.market_data.get_ohlcv_checked",
                   return_value=(None, report)), \
             patch("agmcis.data.market_data.get_price", return_value=100.0):
            signal = pipeline.analyse_symbol("BTC/USDT")

        self.assertFalse(signal.data_ok)
        self.assertFalse(signal.is_tradable)
        self.assertIs(signal.direction, Direction.WAIT)
        self.assertIn("STALE_DATA", signal.data_error)

    def test_bad_data_signal_cannot_become_a_trade_intent(self):
        """最終防線:不可交易的 Signal 轉不成 TradeIntent。"""
        from agmcis.core.errors import TradingRuleViolation
        from agmcis.core.models import Signal, TradeIntent

        signal = Signal(
            symbol="BTC/USDT", market_type="perpetual", direction=Direction.WAIT,
            timeframe="1h", strategy="pipeline", data_ok=False,
        )
        with self.assertRaises(TradingRuleViolation):
            TradeIntent.from_signal(signal)

    def test_scan_market_shim_reports_no_data(self):
        from agmcis.core.models import Signal

        bad = Signal(
            symbol="BTC/USDT", market_type="perpetual", direction=Direction.WAIT,
            timeframe="1h", strategy="pipeline", data_ok=False,
            data_error="stale", reasons=["資料品質不合格"],
        )
        with patch("scanner_service._scan", return_value=[bad]):
            rows = scanner_service.scan_market(["BTC/USDT"])

        self.assertEqual(rows[0]["trade_signal"], "⚪ No Data")
        self.assertFalse(rows[0]["data_ok"])
        self.assertIsNone(rows[0]["stoploss"])


class TestStopLossSideMatchesDirection(unittest.TestCase):
    """
    做空的停損必須在進場價**上方**。

    舊 scanner 一律用 price - atr*2,做空時停損會被放在進場價下方 ——
    那在開倉的瞬間就會觸發。現在停損由策略的 atr_levels() 依方向計算。
    """

    def _indicators(self, **overrides):
        from agmcis.analysis.indicators import Indicators

        base = dict(
            symbol="BTC/USDT", timeframe="1h", price=65000.0,
            ema20=66000.0, ema50=64000.0, ema60=64000.0,
            rsi=60.0, macd=10.0, macd_signal=5.0, macd_hist=5.0,
            adx=30.0, atr=1000.0, bb_upper=67000.0, bb_lower=63000.0,
            volume=150.0, volume_ma20=100.0,
        )
        base.update(overrides)
        return Indicators(**base)

    def test_long_stop_is_below_entry_and_target_above(self):
        from agmcis.analysis.regime import detect
        from agmcis.strategy.registry import StrategyRegistry

        indicators = self._indicators()
        consensus = StrategyRegistry().consensus(indicators, detect(indicators))

        self.assertIs(consensus.direction, Direction.LONG, consensus.blocked_reason)
        self.assertLess(consensus.stop_loss, indicators.price)
        self.assertGreater(consensus.take_profit, indicators.price)

    def test_short_stop_is_above_entry_and_target_below(self):
        from agmcis.analysis.regime import detect
        from agmcis.strategy.registry import StrategyRegistry

        indicators = self._indicators(
            ema20=64000.0, ema50=66000.0, ema60=66000.0,
            rsi=40.0, macd=-10.0, macd_signal=-5.0, macd_hist=-5.0,
        )
        consensus = StrategyRegistry().consensus(indicators, detect(indicators))

        self.assertIs(consensus.direction, Direction.SHORT, consensus.blocked_reason)
        self.assertGreater(consensus.stop_loss, indicators.price)
        self.assertLess(consensus.take_profit, indicators.price)

    def test_atr_levels_never_put_a_stop_on_the_wrong_side(self):
        """跨方向的不變量。"""
        from agmcis.strategy.base import Strategy

        indicators = self._indicators()
        for direction in [Direction.LONG, Direction.SHORT]:
            stop, target = Strategy.atr_levels(indicators, direction)
            if direction is Direction.LONG:
                self.assertLess(stop, indicators.price)
                self.assertGreater(target, indicators.price)
            else:
                self.assertGreater(stop, indicators.price)
                self.assertLess(target, indicators.price)
