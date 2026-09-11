"""
合約規格校準(Phase 11)。

系統裡有幾個數字是憑常識填的,不是 BingX 給的:維持保證金率、費率、資金費率。
它們決定強平價、決定成本、決定回測與模擬盤的結論。

Master Prompt 第五節:**不要靠模型記憶猜 API**。

這一層要保證的不是「一定有真值」(私有端點只能在 VPS 上驗證),
而是:**沒有真值的時候,系統要知道自己在用猜的,而且說出來。**
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.exchange import specs


def snapshot_file(contracts, captured_at=None, testnet=False):
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8",
    )
    json.dump({
        "exchange": "bingx",
        "testnet": testnet,
        "captured_at": captured_at or datetime.now(timezone.utc).isoformat(),
        "contracts": contracts,
    }, handle, ensure_ascii=False)
    handle.close()
    return handle.name


class TestUncalibratedIsVisible(unittest.TestCase):
    """
    最重要的一條:沒有快照時**不可以安靜地用猜測值**。
    安靜地用猜測值,等於系統對自己說謊。
    """

    def setUp(self):
        self.store = specs.SpecStore(path="/nonexistent/specs.json")

    def test_is_calibrated_is_false(self):
        self.assertFalse(self.store.is_calibrated())
        self.assertFalse(self.store.is_calibrated("BTC/USDT"))

    def test_every_value_is_tagged_as_a_guess(self):
        for getter in (self.store.maintenance_margin_ratio, self.store.taker_fee,
                       self.store.maker_fee, self.store.funding_rate_8h):
            with self.subTest(getter=getter.__name__):
                _, source = getter("BTC/USDT")
                self.assertEqual(source, specs.SOURCE_DEFAULT)

    def test_the_report_says_so_in_words(self):
        report = self.store.calibration_report(["BTC/USDT"])

        self.assertFalse(report["calibrated"])
        self.assertTrue(report["warnings"])
        self.assertTrue(any("預設" in w for w in report["warnings"]))

    def test_the_defaults_are_the_conservative_ones(self):
        """猜的時候要往保守的一邊猜:費率高一點、維持保證金率高一點。"""
        self.assertGreaterEqual(specs.DEFAULT_TAKER_FEE, specs.DEFAULT_MAKER_FEE)
        self.assertGreater(specs.DEFAULT_MAINTENANCE_MARGIN_RATIO, 0)


class TestCalibratedValuesAreUsed(unittest.TestCase):

    def setUp(self):
        self.path = snapshot_file({
            "BTC/USDT": {
                "maintenance_margin_ratio": 0.004,
                "taker_fee": 0.0004,
                "maker_fee": 0.0001,
                "funding_rate_8h": 0.00025,
                "max_leverage": 125,
            },
        })
        self.addCleanup(os.unlink, self.path)
        self.store = specs.SpecStore(path=self.path)

    def test_the_exchange_value_wins_over_the_default(self):
        value, source = self.store.maintenance_margin_ratio("BTC/USDT")

        self.assertEqual(value, 0.004)
        self.assertEqual(source, specs.SOURCE_EXCHANGE)

    def test_a_symbol_missing_from_the_snapshot_falls_back_and_says_so(self):
        """有快照不代表每個標的都在裡面。"""
        value, source = self.store.taker_fee("DOGE/USDT")

        self.assertEqual(value, specs.DEFAULT_TAKER_FEE)
        self.assertEqual(source, specs.SOURCE_DEFAULT)
        self.assertFalse(self.store.is_calibrated("DOGE/USDT"))

    def test_a_field_missing_from_a_present_symbol_also_falls_back(self):
        path = snapshot_file({"ETH/USDT": {"taker_fee": 0.0004}})
        self.addCleanup(os.unlink, path)
        store = specs.SpecStore(path=path)

        _, fee_source = store.taker_fee("ETH/USDT")
        value, mmr_source = store.maintenance_margin_ratio("ETH/USDT")

        self.assertEqual(fee_source, specs.SOURCE_EXCHANGE)
        self.assertEqual(mmr_source, specs.SOURCE_DEFAULT)
        self.assertEqual(value, specs.DEFAULT_MAINTENANCE_MARGIN_RATIO)

    def test_the_report_lists_which_fields_are_still_guessed(self):
        path = snapshot_file({"ETH/USDT": {"taker_fee": 0.0004}})
        self.addCleanup(os.unlink, path)

        report = specs.SpecStore(path=path).calibration_report(["ETH/USDT"])

        self.assertTrue(any("maintenance_margin_ratio" in w
                            for w in report["warnings"]))


class TestStaleness(unittest.TestCase):
    """費率與維持保證金分層會變。三個月前的快照不能當成現在的規格。"""

    def test_a_fresh_snapshot_is_not_stale(self):
        path = snapshot_file({"BTC/USDT": {"taker_fee": 0.0004}})
        self.addCleanup(os.unlink, path)

        self.assertFalse(specs.SpecStore(path=path).snapshot.is_stale)

    def test_an_old_snapshot_is_stale_and_warned_about(self):
        old = (datetime.now(timezone.utc)
               - timedelta(days=specs.STALE_AFTER_DAYS + 5)).isoformat()
        path = snapshot_file({"BTC/USDT": {"taker_fee": 0.0004}}, captured_at=old)
        self.addCleanup(os.unlink, path)

        report = specs.SpecStore(path=path).calibration_report(["BTC/USDT"])

        self.assertTrue(report["stale"])
        self.assertTrue(any("過期" in w for w in report["warnings"]))

    def test_a_testnet_snapshot_is_flagged(self):
        """測試網的費率與分層不一定等於正式環境。"""
        path = snapshot_file({"BTC/USDT": {"taker_fee": 0.0004}}, testnet=True)
        self.addCleanup(os.unlink, path)

        report = specs.SpecStore(path=path).calibration_report(["BTC/USDT"])

        self.assertTrue(any("測試" in w for w in report["warnings"]))


class TestBrokenSnapshotIsNotSilentlyIgnored(unittest.TestCase):

    def test_unreadable_json_raises_instead_of_falling_back_quietly(self):
        """
        壞掉的快照被當成「沒有快照」會讓系統用猜測值跑下去,而沒人發現檔案壞了。
        """
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        handle.write("{ 這不是 JSON")
        handle.close()
        self.addCleanup(os.unlink, handle.name)

        with self.assertRaises(Exception):
            specs.SpecStore(path=handle.name).load()


class TestCostModelUsesCalibratedRates(unittest.TestCase):

    def setUp(self):
        from agmcis.execution import paper_costs
        self.paper_costs = paper_costs

        self.path = snapshot_file({
            "BTC/USDT": {
                "maintenance_margin_ratio": 0.004,
                "taker_fee": 0.0009,
                "maker_fee": 0.0003,
                "funding_rate_8h": 0.0007,
            },
        })
        self.addCleanup(os.unlink, self.path)

        specs.set_store(specs.SpecStore(path=self.path))
        self.addCleanup(specs.set_store, None)
        self.addCleanup(self.paper_costs.set_cost_model, None)

    def test_fees_come_from_the_snapshot_for_a_calibrated_symbol(self):
        model = self.paper_costs.build_cost_model("BTC/USDT")

        self.assertEqual(model.taker_fee, 0.0009)
        self.assertEqual(model.funding_rate_8h, 0.0007)

    def test_slippage_and_spread_still_come_from_settings(self):
        """
        那兩個是市場衝擊的估計,交易所不會告訴你,也沒有「正確答案」。
        """
        from agmcis.config import settings

        model = self.paper_costs.build_cost_model("BTC/USDT")

        self.assertEqual(model.slippage_pct, settings.PAPER_SLIPPAGE_PCT)
        self.assertEqual(model.spread_pct, settings.PAPER_SPREAD_PCT)

    def test_an_uncalibrated_symbol_keeps_the_settings_defaults(self):
        from agmcis.config import settings

        model = self.paper_costs.build_cost_model("DOGE/USDT")

        self.assertEqual(model.taker_fee, settings.PAPER_TAKER_FEE)

    def test_the_liquidation_price_uses_the_real_maintenance_margin_ratio(self):
        """
        這個數字直接決定強平價。真實的 0.4% 與猜測的 10% 差非常多 ——
        用猜的值算出來的強平價,在真實行情裡不會準。
        """
        calibrated = self.paper_costs.liquidation_price(
            100, 10, True, symbol="BTC/USDT",
        )
        guessed = self.paper_costs.liquidation_price(
            100, 10, True, symbol="DOGE/USDT",
        )

        self.assertNotAlmostEqual(calibrated, guessed, places=4)
        self.assertAlmostEqual(calibrated, 100 * (1 - (1 - 0.004) / 10), places=6)


class TestVerifyScriptStaysReadOnly(unittest.TestCase):
    """
    --write-specs 只是把已經抓到的 fetch_* 結果寫成 JSON。
    這支腳本在有金鑰的機器上跑,絕不能出現任何寫入交易所的呼叫。
    """

    def test_the_script_never_calls_a_write_endpoint(self):
        import ast

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "scripts", "verify_bingx.py",
        )
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)

        forbidden = {
            "create_order", "cancel_order", "set_leverage", "set_margin_mode",
            "withdraw", "transfer", "close_position", "create_market_order",
        }

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = (
                func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name)
                else None
            )
            with self.subTest(call=called):
                self.assertNotIn(called, forbidden)


class TestFundingRateFieldName(unittest.TestCase):
    """
    Agent 管線讀的欄位名必須與 adapter 產生的一致。

    我第一版寫成 funding.get("rate"),但 adapter 給的是 funding_rate ——
    那會永遠拿到 None,FundingAgent 永遠棄權,而且完全不會報錯。
    """

    def test_the_pipeline_reads_the_key_the_adapter_writes(self):
        import inspect

        from agmcis.exchange.bingx import adapter as adapter_module
        from agmcis.signal import agent_pipeline

        adapter_source = inspect.getsource(adapter_module.BingXAdapter.get_funding_rate)
        pipeline_source = inspect.getsource(agent_pipeline.build_context)

        self.assertIn('"funding_rate"', adapter_source)
        self.assertIn('funding.get("funding_rate")', pipeline_source)

    def test_a_funding_agent_actually_votes_when_the_rate_is_extreme(self):
        """欄位名接錯的症狀就是這個 Agent 永遠棄權,所以直接測它會投票。"""
        from agmcis.agents.base import AgentContext, Vote
        from agmcis.agents.builtin import FundingAgent
        from agmcis.analysis.indicators import Indicators

        context = AgentContext(
            symbol="BTC/USDT",
            indicators=Indicators(symbol="BTC/USDT", timeframe="1h",
                                  price=100.0, data_ok=True),
            funding_rate=0.005,
        )

        self.assertIs(FundingAgent().analyse(context).vote, Vote.SHORT)


class TestMaintenanceMarginTiers(unittest.TestCase):
    """
    BingX 依倉位大小分層:倉位越大維持保證金率越高、強平價越近。
    用單一數字會**低估大倉位的強平風險** —— 而那決定的是會不會爆倉。
    """

    TIERS = [
        {"notional_floor": 0, "mmr": 0.004, "max_leverage": 125},
        {"notional_floor": 50000, "mmr": 0.005, "max_leverage": 100},
        {"notional_floor": 250000, "mmr": 0.01, "max_leverage": 50},
    ]

    def setUp(self):
        self.path = snapshot_file({
            "BTC/USDT": {
                "maintenance_margin_ratio": 0.004,
                "maintenance_margin_tiers": self.TIERS,
            },
            "ETH/USDT": {"maintenance_margin_ratio": 0.005},
        })
        self.addCleanup(os.unlink, self.path)
        self.store = specs.SpecStore(path=self.path)

    def test_a_small_position_uses_the_first_tier(self):
        value, source = self.store.maintenance_margin_ratio(
            "BTC/USDT", notional=1000,
        )

        self.assertEqual(value, 0.004)
        self.assertEqual(source, specs.SOURCE_EXCHANGE)

    def test_a_large_position_uses_a_higher_ratio(self):
        small, _ = self.store.maintenance_margin_ratio("BTC/USDT", notional=1000)
        large, _ = self.store.maintenance_margin_ratio("BTC/USDT", notional=300000)

        self.assertGreater(large, small)

    def test_the_boundary_belongs_to_the_higher_tier(self):
        """floor 剛好等於名目價值時屬於那一層,不是前一層。"""
        value, _ = self.store.maintenance_margin_ratio("BTC/USDT", notional=50000)

        self.assertEqual(value, 0.005)

    def test_tiers_are_sorted_even_if_the_data_is_not(self):
        path = snapshot_file({"X/USDT": {"maintenance_margin_tiers": [
            {"notional_floor": 250000, "mmr": 0.01},
            {"notional_floor": 0, "mmr": 0.004},
            {"notional_floor": 50000, "mmr": 0.005},
        ]}})
        self.addCleanup(os.unlink, path)

        value, _ = specs.SpecStore(path=path).maintenance_margin_ratio(
            "X/USDT", notional=1000,
        )

        self.assertEqual(value, 0.004)

    def test_a_symbol_without_tiers_falls_back_to_the_single_number(self):
        value, source = self.store.maintenance_margin_ratio(
            "ETH/USDT", notional=999999,
        )

        self.assertEqual(value, 0.005)
        self.assertEqual(source, specs.SOURCE_EXCHANGE)

    def test_malformed_tier_data_is_ignored_not_guessed(self):
        path = snapshot_file({"X/USDT": {
            "maintenance_margin_ratio": 0.006,
            "maintenance_margin_tiers": [{"notional_floor": "不是數字", "mmr": 0.01}],
        }})
        self.addCleanup(os.unlink, path)

        store = specs.SpecStore(path=path)

        with patch.object(specs, "logger"):
            value, _ = store.maintenance_margin_ratio("X/USDT", notional=1000)

        self.assertEqual(value, 0.006)

    def test_max_leverage_comes_from_the_tier(self):
        self.assertEqual(
            self.store.max_leverage_for_notional("BTC/USDT", 300000), 50.0,
        )

    def test_missing_tiers_are_named_in_the_calibration_report(self):
        report = self.store.calibration_report(["BTC/USDT", "ETH/USDT"])

        self.assertTrue(any(
            "ETH/USDT" in w and "分層" in w for w in report["warnings"]
        ))
        self.assertFalse(any(
            "BTC/USDT" in w and "分層" in w for w in report["warnings"]
        ))


class TestLiquidationPriceUsesTheTier(unittest.TestCase):

    def setUp(self):
        from agmcis.execution import paper_costs
        self.paper_costs = paper_costs

        self.path = snapshot_file({"BTC/USDT": {
            "maintenance_margin_ratio": 0.004,
            "maintenance_margin_tiers": TestMaintenanceMarginTiers.TIERS,
        }})
        self.addCleanup(os.unlink, self.path)

        specs.set_store(specs.SpecStore(path=self.path))
        self.addCleanup(specs.set_store, None)
        self.addCleanup(paper_costs.set_cost_model, None)

    def test_a_bigger_position_liquidates_sooner(self):
        """
        同樣的槓桿,倉位越大強平價越接近進場價。
        忽略這件事等於低估大倉位的風險。
        """
        small = self.paper_costs.liquidation_price(
            100, 10, True, symbol="BTC/USDT", notional=1000,
        )
        large = self.paper_costs.liquidation_price(
            100, 10, True, symbol="BTC/USDT", notional=300000,
        )

        self.assertGreater(large, small)

    def test_without_a_notional_it_uses_the_flat_ratio(self):
        """沒帶名目價值時行為不變 —— 舊呼叫端不會壞掉。"""
        price = self.paper_costs.liquidation_price(100, 10, True, symbol="BTC/USDT")

        self.assertAlmostEqual(price, 100 * (1 - (1 - 0.004) / 10), places=6)


if __name__ == "__main__":
    unittest.main()
