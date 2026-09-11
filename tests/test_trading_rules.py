"""
合約規則:快取、進位、驗證。

這一層只判斷「交易所收不收」,不碰風控。
風控決定要不要開、開多大;這裡決定這個數字合不合法。
"""
import unittest
from unittest.mock import MagicMock

from agmcis.core.enums import Direction, MarketType
from agmcis.core.errors import TradingRuleViolation
from agmcis.core.models import TradingRules
from agmcis.exchange import trading_rules as tr


def rules(**overrides):
    base = dict(
        exchange="bingx", market_type=MarketType.PERPETUAL, symbol="BTC/USDT:USDT",
        tick_size=0.1, step_size=0.001, min_qty=0.001, max_qty=1000,
        min_notional=5, contract_size=1, max_leverage=125,
    )
    base.update(overrides)
    return TradingRules(**base)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TestRegistryCaching(unittest.TestCase):

    def setUp(self):
        self.clock = FakeClock()
        self.adapter = MagicMock()
        self.adapter.name = "bingx"
        self.adapter.get_trading_rules.return_value = rules()
        self.registry = tr.TradingRulesRegistry(
            adapter=self.adapter, ttl_seconds=60, clock=self.clock,
        )

    def test_second_lookup_hits_the_cache(self):
        """每次下單都打 load_markets 太慢,但規則又不能寫死。"""
        self.registry.get("BTC/USDT")
        self.registry.get("BTC/USDT")
        self.adapter.get_trading_rules.assert_called_once()

    def test_cache_expires_after_ttl(self):
        self.registry.get("BTC/USDT")
        self.clock.advance(61)
        self.registry.get("BTC/USDT")
        self.assertEqual(self.adapter.get_trading_rules.call_count, 2)

    def test_refresh_forces_a_fetch(self):
        self.registry.get("BTC/USDT")
        self.registry.get("BTC/USDT", refresh=True)
        self.assertEqual(self.adapter.get_trading_rules.call_count, 2)

    def test_market_types_do_not_share_a_cache_entry(self):
        """Standard 與 Perpetual 的同一個 symbol 規則不同。"""
        self.registry.get("BTC/USDT", MarketType.PERPETUAL)
        self.registry.get("BTC/USDT", MarketType.STANDARD)
        self.assertEqual(self.adapter.get_trading_rules.call_count, 2)
        self.assertEqual(self.registry.cached_count(), 2)

    def test_invalidate_single_symbol(self):
        self.registry.get("BTC/USDT")
        self.registry.invalidate("BTC/USDT")
        self.registry.get("BTC/USDT")
        self.assertEqual(self.adapter.get_trading_rules.call_count, 2)


class TestRounding(unittest.TestCase):

    def test_quantity_always_rounds_down(self):
        """超出額度的部分交易所會直接拒單,寧可少一點。"""
        r = rules()
        self.assertEqual(tr.round_quantity(0.0012345, r), 0.001)
        self.assertEqual(tr.round_quantity(0.9999, r), 0.999)

    def test_quantity_already_on_step_is_unchanged(self):
        self.assertEqual(tr.round_quantity(0.005, rules()), 0.005)

    def test_long_stop_rounds_down_to_exit_earlier(self):
        """做多的停損往下取 —— 更早離場比更晚離場安全。"""
        self.assertEqual(
            tr.round_price(65000.37, rules(), Direction.LONG, is_stop=True), 65000.3
        )

    def test_short_stop_rounds_up_to_exit_earlier(self):
        self.assertEqual(
            tr.round_price(65000.33, rules(), Direction.SHORT, is_stop=True), 65000.4
        )

    def test_long_take_profit_rounds_up(self):
        self.assertEqual(
            tr.round_price(65000.33, rules(), Direction.LONG, is_stop=False), 65000.4
        )

    def test_no_direction_rounds_down(self):
        self.assertEqual(tr.round_price(65000.37, rules()), 65000.3)

    def test_precision_as_decimal_places_is_handled(self):
        """ccxt 的 precision 可能是小數位數(3)也可能是最小單位(0.001)。"""
        self.assertEqual(tr.round_quantity(0.0012345, rules(step_size=3)), 0.001)

    def test_missing_step_leaves_value_alone(self):
        self.assertEqual(tr.round_quantity(0.0012345, rules(step_size=None)), 0.0012345)


class TestValidation(unittest.TestCase):

    def test_valid_order_passes(self):
        ok, problems = tr.validate_quantity(0.01, 65000, rules())
        self.assertTrue(ok)
        self.assertEqual(problems, [])

    def test_below_min_quantity_is_rejected(self):
        ok, problems = tr.validate_quantity(0.0001, 65000, rules())
        self.assertFalse(ok)
        self.assertTrue(any("最小下單量" in p for p in problems))

    def test_above_max_quantity_is_rejected(self):
        ok, problems = tr.validate_quantity(5000, 65000, rules())
        self.assertFalse(ok)
        self.assertTrue(any("最大下單量" in p for p in problems))

    def test_non_step_multiple_is_rejected(self):
        ok, problems = tr.validate_quantity(0.0015555, 65000, rules())
        self.assertFalse(ok)
        self.assertTrue(any("step_size" in p for p in problems))

    def test_below_min_notional_is_rejected(self):
        ok, problems = tr.validate_quantity(0.001, 1.0, rules())
        self.assertFalse(ok)
        self.assertTrue(any("名目價值" in p for p in problems))

    def test_non_tick_price_is_rejected(self):
        ok, problems = tr.validate_price(65000.37, rules())
        self.assertFalse(ok)
        self.assertTrue(any("tick_size" in p for p in problems))

    def test_leverage_above_contract_limit_is_rejected(self):
        ok, problems = tr.validate_leverage(200, rules())
        self.assertFalse(ok)
        self.assertTrue(any("上限" in p for p in problems))

    def test_all_problems_are_reported_at_once(self):
        """下單被拒之後一次修好,比來回試三次好。"""
        with self.assertRaises(TradingRuleViolation) as ctx:
            tr.assert_valid_order(0.0001, 65000.37, rules(), leverage=200)

        message = str(ctx.exception)
        self.assertIn("tick_size", message)
        self.assertIn("最小下單量", message)
        self.assertIn("上限", message)

    def test_assert_passes_for_a_good_order(self):
        self.assertTrue(tr.assert_valid_order(0.01, 65000.0, rules(), leverage=5))


class TestNotionalConversion(unittest.TestCase):

    def test_converts_and_rounds_to_step(self):
        qty, problems = tr.quantity_for_notional(1000, 65000, rules())
        self.assertEqual(qty, 0.015)
        self.assertEqual(problems, [])

    def test_too_small_notional_returns_none(self):
        """下不了單就明確回 None,不要回一個會被拒單的數字。"""
        qty, problems = tr.quantity_for_notional(1, 65000, rules())
        self.assertIsNone(qty)
        self.assertTrue(problems)

    def test_contract_size_is_applied(self):
        qty, _ = tr.quantity_for_notional(1000, 100, rules(contract_size=10, step_size=0.1))
        self.assertAlmostEqual(qty, 1.0)

    def test_bad_price_returns_none(self):
        qty, problems = tr.quantity_for_notional(1000, 0, rules())
        self.assertIsNone(qty)
        self.assertTrue(problems)
