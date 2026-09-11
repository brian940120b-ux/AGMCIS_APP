"""
Trading Rules Engine。

職責分界:這一層**不做風險判斷**。它不知道帳戶回撤多少、今天虧了多少。
它只回答「這張單送出去,交易所會不會因為格式問題拒絕」。

最重要的不變量:**調整永遠只會讓倉位變小,不會變大。**
自動進位不可以變成偷偷放寬風控決定的倉位大小。
"""
import unittest
from unittest.mock import MagicMock

from agmcis.core.enums import MarketType, OrderSide, OrderType, PositionSide
from agmcis.core.errors import TradingRuleViolation
from agmcis.core.models import TradeIntent, TradingRules
from agmcis.execution import client_order_id as coid
from agmcis.execution.rules_engine import (
    OrderRequest,
    TradingContext,
    TradingRulesEngine,
)


def make_rules(**overrides):
    base = dict(
        exchange="bingx", market_type=MarketType.PERPETUAL, symbol="BTC/USDT:USDT",
        tick_size=0.1, step_size=0.001, min_qty=0.001, max_qty=1000,
        min_notional=5, contract_size=1, max_leverage=125,
    )
    base.update(overrides)
    return TradingRules(**base)


def make_engine(rules=None):
    registry = MagicMock()
    registry.get.return_value = rules or make_rules()
    return TradingRulesEngine(registry=registry)


def long_intent(**overrides):
    base = dict(symbol="BTC/USDT", market_type="perpetual", direction="做多",
                entry=65000.0, stop_loss=63000.0, take_profit=69000.0)
    base.update(overrides)
    return TradeIntent(**base)


def short_intent(**overrides):
    base = dict(symbol="BTC/USDT", market_type="perpetual", direction="做空",
                entry=65000.0, stop_loss=67000.0, take_profit=61000.0)
    base.update(overrides)
    return TradeIntent(**base)


class TestHappyPath(unittest.TestCase):

    def setUp(self):
        self.engine = make_engine()

    def test_valid_order_is_accepted(self):
        result = self.engine.validate(
            long_intent(), size_usdt=1000, leverage=5,
            context=TradingContext(available_balance=5000),
        )
        self.assertTrue(result.ok, result.reason)
        self.assertIsInstance(result.order, OrderRequest)

    def test_quantity_is_derived_from_margin_times_leverage(self):
        result = self.engine.validate(
            long_intent(), size_usdt=1000, leverage=5, context=TradingContext(),
        )
        # 名目 5000 / 價格 65000 = 0.0769...,捨去到 step 0.001
        self.assertEqual(result.order.quantity, 0.076)

    def test_market_order_carries_no_limit_price(self):
        """市價單不該帶價格,但仍然要記得決策當時的參考價。"""
        result = self.engine.validate(
            long_intent(), size_usdt=1000, leverage=5,
            order_type=OrderType.MARKET, context=TradingContext(),
        )
        self.assertIsNone(result.order.price)
        self.assertEqual(result.order.reference_price, 65000.0)
        self.assertIsNotNone(result.order.notional)

    def test_limit_order_carries_a_rounded_price(self):
        from agmcis.exchange import trading_rules as tr

        result = self.engine.validate(
            long_intent(entry=65000.37), size_usdt=1000, leverage=5,
            order_type=OrderType.LIMIT, context=TradingContext(),
        )
        self.assertIsNotNone(result.order.price)

        # 用驗證函式判斷是不是 tick 的整數倍。
        # 不要用浮點數取餘:65000.4 % 0.1 會得到 0.0999... 而不是 0。
        ok, problems = tr.validate_price(result.order.price, make_rules())
        self.assertTrue(ok, problems)

    def test_side_follows_direction(self):
        self.assertIs(
            self.engine.validate(long_intent(), 1000, 5).order.side, OrderSide.BUY
        )
        self.assertIs(
            self.engine.validate(short_intent(), 1000, 5).order.side, OrderSide.SELL
        )


class TestAdjustmentsOnlyShrink(unittest.TestCase):
    """
    自動進位不可以變成偷偷放寬風控的決定。
    數量只能往下,停損只能往「更早離場」的方向。
    """

    def setUp(self):
        self.engine = make_engine()

    def test_quantity_never_rounds_up(self):
        result = self.engine.validate(
            long_intent(), size_usdt=1000, leverage=5, context=TradingContext(),
        )
        requested_notional = 1000 * 5
        self.assertLessEqual(result.order.notional, requested_notional)

    def test_long_stop_loss_rounds_down(self):
        result = self.engine.validate(
            long_intent(stop_loss=63000.44), size_usdt=1000, leverage=5,
        )
        self.assertEqual(result.order.stop_loss, 63000.4)

    def test_short_stop_loss_rounds_up(self):
        result = self.engine.validate(
            short_intent(stop_loss=67000.33), size_usdt=1000, leverage=5,
        )
        self.assertEqual(result.order.stop_loss, 67000.4)

    def test_adjustments_are_reported_not_hidden(self):
        result = self.engine.validate(
            long_intent(stop_loss=63000.44, take_profit=69000.88),
            size_usdt=1000, leverage=5,
        )
        self.assertTrue(result.adjustments)
        self.assertTrue(any("停損" in a for a in result.adjustments))


class TestRejections(unittest.TestCase):

    def setUp(self):
        self.engine = make_engine()

    def test_insufficient_balance(self):
        result = self.engine.validate(
            long_intent(), size_usdt=9999, leverage=5,
            context=TradingContext(available_balance=100),
        )
        self.assertFalse(result.ok)
        self.assertIn("可用餘額", result.reason)

    def test_leverage_above_contract_limit(self):
        result = self.engine.validate(long_intent(), size_usdt=1000, leverage=200)
        self.assertFalse(result.ok)
        self.assertIn("上限", result.reason)

    def test_notional_too_small_to_place(self):
        result = self.engine.validate(long_intent(), size_usdt=0.01, leverage=1)
        self.assertFalse(result.ok)

    def test_duplicate_position_is_blocked(self):
        result = self.engine.validate(
            long_intent(), size_usdt=1000, leverage=5,
            context=TradingContext(open_symbols=["BTC/USDT"]),
        )
        self.assertFalse(result.ok)
        self.assertIn("不重複開倉", result.reason)

    def test_duplicate_client_order_id_is_blocked(self):
        result = self.engine.validate(
            long_intent(), size_usdt=1000, leverage=5,
            client_order_id="AGMCIS-20260911-BTC-000001",
            context=TradingContext(
                pending_client_order_ids=["AGMCIS-20260911-BTC-000001"]
            ),
        )
        self.assertFalse(result.ok)
        self.assertIn("重複", result.reason)

    def test_unsupported_order_type_is_blocked(self):
        result = self.engine.validate(
            long_intent(), size_usdt=1000, leverage=5,
            order_type=OrderType.TRAILING_STOP,
            context=TradingContext(supported_order_types=["market", "limit"]),
        )
        self.assertFalse(result.ok)
        self.assertIn("不支援", result.reason)

    def test_reduce_only_without_a_position_is_blocked(self):
        result = self.engine.validate(
            long_intent(), size_usdt=1000, leverage=5, reduce_only=True,
            context=TradingContext(open_symbols=[]),
        )
        self.assertFalse(result.ok)
        self.assertIn("reduce-only", result.reason)

    def test_reduce_only_with_a_position_is_allowed(self):
        result = self.engine.validate(
            long_intent(), size_usdt=1000, leverage=5, reduce_only=True,
            context=TradingContext(open_symbols=["BTC/USDT"]),
        )
        self.assertTrue(result.ok, result.reason)

    def test_zero_margin_is_blocked(self):
        self.assertFalse(self.engine.validate(long_intent(), size_usdt=0, leverage=5).ok)

    def test_missing_contract_rules_is_a_clean_rejection(self):
        registry = MagicMock()
        registry.get.side_effect = RuntimeError("no such market")
        engine = TradingRulesEngine(registry=registry)

        result = engine.validate(long_intent(), size_usdt=1000, leverage=5)
        self.assertFalse(result.ok)
        self.assertIn("取不到合約規則", result.reason)

    def test_all_violations_reported_at_once(self):
        """下單被拒之後一次修好,比來回試三次好。"""
        result = self.engine.validate(
            long_intent(), size_usdt=9999, leverage=200,
            context=TradingContext(available_balance=10, open_symbols=["BTC/USDT"]),
        )
        self.assertGreaterEqual(len(result.violations), 3)

    def test_raise_if_invalid(self):
        result = self.engine.validate(long_intent(), size_usdt=1000, leverage=200)
        with self.assertRaises(TradingRuleViolation):
            result.raise_if_invalid()


class TestPositionMode(unittest.TestCase):
    """
    One-Way 下 positionSide 是 BOTH;Hedge 下要明確指定。
    搞錯會讓平倉單變成反向開倉。
    """

    def setUp(self):
        self.engine = make_engine()

    def test_oneway_uses_both(self):
        result = self.engine.validate(
            long_intent(), 1000, 5, context=TradingContext(position_mode="oneway"),
        )
        self.assertIs(result.order.position_side, PositionSide.BOTH)

    def test_hedge_long(self):
        result = self.engine.validate(
            long_intent(), 1000, 5, context=TradingContext(position_mode="hedge"),
        )
        self.assertIs(result.order.position_side, PositionSide.LONG)

    def test_hedge_short(self):
        result = self.engine.validate(
            short_intent(), 1000, 5, context=TradingContext(position_mode="hedge"),
        )
        self.assertIs(result.order.position_side, PositionSide.SHORT)


class TestSeparationOfConcerns(unittest.TestCase):
    """
    這一層不做風險判斷。帳戶回撤、今日虧損、連續虧損都不是它該知道的事。
    """

    def test_engine_does_not_import_risk_control(self):
        import agmcis.execution.rules_engine as mod
        source = open(mod.__file__, encoding="utf-8").read()
        self.assertNotIn("risk_control", source)
        self.assertNotIn("risk_limits", source)

    def test_context_has_no_risk_fields(self):
        fields = set(TradingContext.__dataclass_fields__)
        for forbidden in ["drawdown", "daily_loss", "consecutive_losses", "equity"]:
            self.assertNotIn(forbidden, fields)


class TestClientOrderId(unittest.TestCase):

    def setUp(self):
        coid.reset_sequence()

    def test_generated_ids_are_unique(self):
        ids = {coid.generate("BTC/USDT") for _ in range(100)}
        self.assertEqual(len(ids), 100)

    def test_id_is_within_length_and_charset(self):
        for symbol in ["BTC/USDT:USDT", "1000PEPE/USDT", "DOGE/USDT"]:
            cid = coid.generate(symbol)
            self.assertTrue(coid.is_valid(cid), cid)
            self.assertLessEqual(len(cid), coid.MAX_LENGTH)

    def test_ours_vs_theirs(self):
        """對帳時要能分辨哪些單是 AGMCIS 開的。"""
        self.assertTrue(coid.is_ours(coid.generate("BTC/USDT")))
        self.assertFalse(coid.is_ours("someone-else-123"))
        self.assertFalse(coid.is_ours(None))

    def test_rejects_unsafe_characters(self):
        self.assertFalse(coid.is_valid("AGMCIS/BTC"))
        self.assertFalse(coid.is_valid("AGMCIS BTC"))

    def test_engine_rejects_bad_client_order_id(self):
        engine = make_engine()
        result = engine.validate(
            long_intent(), 1000, 5, client_order_id="bad/id",
        )
        self.assertFalse(result.ok)
        self.assertIn("client order id", result.reason)
