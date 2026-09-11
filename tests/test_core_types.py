"""
core 型別。

重點是 TradeIntent 在建構時就強制停損 —— 一個沒有停損、或停損放在錯誤方向的
TradeIntent 根本無法被建立出來。這讓「開倉必須有停損」變成型別層級的保證,
不是靠每個呼叫端自己記得檢查。
"""
import json
import unittest

from agmcis.core.enums import (
    ConfidenceBand,
    Direction,
    MarketType,
    OrderSide,
    OrderState,
    TradingMode,
)
from agmcis.core.errors import TradingRuleViolation
from agmcis.core.models import ClosedTrade, Position, RiskDecision, Signal, TradeIntent


class TestDirectionEnum(unittest.TestCase):

    def test_parses_chinese_and_english(self):
        for raw in ["做多", "LONG", "long", "buy", "多", "bullish"]:
            self.assertIs(Direction.parse(raw), Direction.LONG, raw)
        for raw in ["做空", "SHORT", "sell", "空", "bearish"]:
            self.assertIs(Direction.parse(raw), Direction.SHORT, raw)

    def test_unknown_returns_none_never_guesses(self):
        for raw in ["garbage", "", None, "🟡 Hold"]:
            self.assertIsNone(Direction.parse(raw), raw)

    def test_wait_is_not_directional(self):
        self.assertFalse(Direction.WAIT.is_directional)
        self.assertTrue(Direction.LONG.is_directional)

    def test_opposite(self):
        self.assertIs(Direction.LONG.opposite, Direction.SHORT)
        self.assertIs(Direction.SHORT.opposite, Direction.LONG)
        self.assertIs(Direction.WAIT.opposite, Direction.WAIT)

    def test_order_side(self):
        self.assertIs(Direction.LONG.order_side, OrderSide.BUY)
        self.assertIs(Direction.SHORT.order_side, OrderSide.SELL)
        self.assertIsNone(Direction.WAIT.order_side)

    def test_price_change_sign(self):
        self.assertAlmostEqual(Direction.LONG.price_change_pct(100, 110), 0.10)
        self.assertAlmostEqual(Direction.SHORT.price_change_pct(100, 90), 0.10)
        self.assertAlmostEqual(Direction.SHORT.price_change_pct(100, 110), -0.10)

    def test_price_change_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            Direction.LONG.price_change_pct(0, 110)
        with self.assertRaises(ValueError):
            Direction.WAIT.price_change_pct(100, 110)


class TestOtherEnums(unittest.TestCase):

    def test_market_type_aliases(self):
        self.assertIs(MarketType.parse("swap"), MarketType.PERPETUAL)
        self.assertIs(MarketType.parse("perpetual"), MarketType.PERPETUAL)
        self.assertIs(MarketType.parse("standard"), MarketType.STANDARD)

    def test_unknown_order_states_need_reconciliation_not_retry(self):
        for state in [OrderState.UNKNOWN, OrderState.TIMEOUT, OrderState.SUBMITTING]:
            self.assertTrue(state.needs_reconciliation, state)
        self.assertFalse(OrderState.FILLED.needs_reconciliation)

    def test_terminal_states(self):
        self.assertTrue(OrderState.CLOSED.is_terminal)
        self.assertTrue(OrderState.REJECTED.is_terminal)
        self.assertFalse(OrderState.PARTIALLY_FILLED.is_terminal)

    def test_only_live_risks_real_money(self):
        self.assertTrue(TradingMode.LIVE.risks_real_money)
        for mode in [TradingMode.PAPER, TradingMode.TEST, TradingMode.MANUAL]:
            self.assertFalse(mode.risks_real_money, mode)
        self.assertTrue(TradingMode.TEST.sends_real_orders)
        self.assertFalse(TradingMode.PAPER.sends_real_orders)

    def test_confidence_band_treats_none_as_no_trade(self):
        self.assertIs(ConfidenceBand.of(None), ConfidenceBand.NO_TRADE)
        self.assertIs(ConfidenceBand.of(95), ConfidenceBand.EXTREME)
        self.assertIs(ConfidenceBand.of(42), ConfidenceBand.NO_TRADE)


class TestTradeIntentEnforcesStopLoss(unittest.TestCase):

    def _intent(self, **overrides):
        kwargs = {
            "symbol": "BTC/USDT", "market_type": "perpetual", "direction": "做多",
            "entry": 100.0, "stop_loss": 97.0, "take_profit": 106.0,
        }
        kwargs.update(overrides)
        return TradeIntent(**kwargs)

    def test_rejects_missing_stop_loss(self):
        with self.assertRaises(TradingRuleViolation) as ctx:
            self._intent(stop_loss=None)
        self.assertIn("沒有停損", str(ctx.exception))

    def test_rejects_long_stop_loss_above_entry(self):
        with self.assertRaises(TradingRuleViolation):
            self._intent(direction="做多", stop_loss=103.0)

    def test_rejects_short_stop_loss_below_entry(self):
        with self.assertRaises(TradingRuleViolation):
            self._intent(direction="做空", stop_loss=97.0, take_profit=94.0)

    def test_rejects_take_profit_on_wrong_side(self):
        with self.assertRaises(TradingRuleViolation):
            self._intent(take_profit=94.0)

    def test_rejects_wait_direction(self):
        with self.assertRaises(TradingRuleViolation):
            self._intent(direction="觀望")

    def test_rejects_unknown_direction(self):
        with self.assertRaises(ValueError):
            self._intent(direction="nonsense")

    def test_rejects_non_positive_entry(self):
        with self.assertRaises(TradingRuleViolation):
            self._intent(entry=0)

    def test_accepts_valid_long_and_computes_geometry(self):
        intent = self._intent()
        self.assertAlmostEqual(intent.stop_distance_pct, 3.0)
        self.assertEqual(intent.risk_reward, 2.0)
        self.assertIs(intent.order_side, OrderSide.BUY)

    def test_accepts_valid_short(self):
        intent = self._intent(direction="做空", stop_loss=103.0, take_profit=94.0)
        self.assertAlmostEqual(intent.stop_distance_pct, 3.0)
        self.assertEqual(intent.risk_reward, 2.0)
        self.assertIs(intent.order_side, OrderSide.SELL)

    def test_take_profit_is_optional(self):
        intent = self._intent(take_profit=None)
        self.assertIsNone(intent.risk_reward)

    def test_intent_carries_no_size_or_leverage(self):
        """倉位大小與槓桿是 Risk Engine 的職責,不該由 Agent 決定。"""
        intent = self._intent()
        self.assertFalse(hasattr(intent, "size_usdt"))
        self.assertFalse(hasattr(intent, "leverage"))

    def test_json_serializable(self):
        json.dumps(self._intent().to_dict())


class TestSignalToIntentGate(unittest.TestCase):

    def _signal(self, **overrides):
        kwargs = {
            "symbol": "ETH/USDT", "market_type": "perpetual", "direction": "做多",
            "timeframe": "1h", "strategy": "test", "entry": 100.0,
            "stop_loss": 97.0, "take_profit": 106.0,
        }
        kwargs.update(overrides)
        return Signal(**kwargs)

    def test_bad_data_signal_cannot_become_an_intent(self):
        signal = self._signal(data_ok=False, data_error="ohlcv_fetch_failed")
        self.assertFalse(signal.is_tradable)
        with self.assertRaises(TradingRuleViolation):
            TradeIntent.from_signal(signal)

    def test_wait_signal_cannot_become_an_intent(self):
        signal = self._signal(direction="觀望")
        self.assertFalse(signal.is_tradable)
        with self.assertRaises(TradingRuleViolation):
            TradeIntent.from_signal(signal)

    def test_signal_without_stop_loss_cannot_become_an_intent(self):
        signal = self._signal(stop_loss=None)
        self.assertFalse(signal.is_tradable)
        with self.assertRaises(TradingRuleViolation):
            TradeIntent.from_signal(signal)

    def test_valid_signal_converts(self):
        intent = TradeIntent.from_signal(self._signal())
        self.assertEqual(intent.symbol, "ETH/USDT")
        self.assertEqual(intent.risk_reward, 2.0)

    def test_signal_risk_reward(self):
        self.assertEqual(self._signal().risk_reward, 2.0)
        self.assertIsNone(self._signal(take_profit=None).risk_reward)


class TestPositionMath(unittest.TestCase):

    def _position(self, **overrides):
        kwargs = {
            "symbol": "BTC/USDT", "market_type": "perpetual", "direction": "做多",
            "entry_price": 100.0, "size_usdt": 1000.0, "leverage": 5.0,
            "stop_loss": 97.0,
        }
        kwargs.update(overrides)
        return Position(**kwargs)

    def test_notional_is_margin_times_leverage(self):
        self.assertEqual(self._position().notional, 5000.0)

    def test_unrealized_pnl_includes_leverage(self):
        self.assertAlmostEqual(self._position().unrealized_pnl(110), 500.0)

    def test_roi_is_on_margin(self):
        self.assertAlmostEqual(self._position().roi_pct(110), 50.0)

    def test_pnl_and_roi_are_consistent(self):
        position = self._position(leverage=3.0, size_usdt=800.0)
        pnl = position.unrealized_pnl(107.5)
        roi = position.roi_pct(107.5)
        self.assertAlmostEqual(pnl, position.size_usdt * roi / 100)

    def test_short_profits_when_price_falls(self):
        position = self._position(direction="做空", stop_loss=103.0)
        self.assertAlmostEqual(position.unrealized_pnl(90), 500.0)

    def test_missing_price_returns_none(self):
        self.assertIsNone(self._position().unrealized_pnl(None))

    def test_position_without_stop_loss_is_unprotected(self):
        self.assertTrue(self._position().is_protected)
        self.assertFalse(self._position(stop_loss=None).is_protected)


class TestRiskDecision(unittest.TestCase):

    def test_risk_usdt_uses_stop_distance(self):
        intent = TradeIntent(
            symbol="BTC/USDT", market_type="perpetual", direction="做多",
            entry=100.0, stop_loss=98.0,
        )
        decision = RiskDecision(intent=intent, approved=True, size_usdt=1000, leverage=3)
        self.assertEqual(decision.notional, 3000)
        self.assertAlmostEqual(decision.risk_usdt, 60.0)

    def test_rejected_decision_has_no_sizing(self):
        intent = TradeIntent(
            symbol="BTC/USDT", market_type="perpetual", direction="做多",
            entry=100.0, stop_loss=98.0,
        )
        decision = RiskDecision(intent=intent, approved=False, reason="MAX_DAILY_LOSS")
        self.assertIsNone(decision.notional)
        self.assertIsNone(decision.risk_usdt)


class TestClosedTradeBasis(unittest.TestCase):

    def _trade(self, basis):
        return ClosedTrade(
            symbol="BTC/USDT", direction="做多", entry_price=100.0, exit_price=110.0,
            size_usdt=1000.0, leverage=5.0, pnl_usdt=500.0, pnl_pct=50.0,
            pnl_basis=basis,
        )

    def test_legacy_rows_are_flagged_as_not_comparable(self):
        self.assertFalse(self._trade("LEGACY_UNLEVERAGED").pnl_is_comparable)
        self.assertTrue(self._trade("LEVERAGED").pnl_is_comparable)

    def test_win_detection(self):
        self.assertTrue(self._trade("LEVERAGED").is_win)
