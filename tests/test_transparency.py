"""
透明度面板(Phase 14)。

Phase 9 到 13 加的機制到目前為止只在 log 裡。看不到就等於不存在 ——
而且你分不出「沒有問題」與「壞掉了所以什麼都沒回報」。

這一頁全部唯讀。有測試掃描原始碼確認沒有任何下單路徑。
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.transparency as transparency


class TestEverythingIsReadOnly(unittest.TestCase):

    def test_the_module_cannot_trade(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "api", "transparency.py",
        )
        with open(path, encoding="utf-8") as handle:
            source = handle.read()

        for banned in ("create_paper_trade", "close_paper_trade", "create_order",
                       "run_auto_trader", "close_position", "update_account"):
            with self.subTest(name=banned):
                self.assertNotIn(banned, source)

    def test_all_routes_are_get(self):
        """GET 依定義不該變更狀態,而這一頁本來就只是看。"""
        for route in transparency.router.routes:
            with self.subTest(path=route.path):
                self.assertEqual(set(route.methods), {"GET"})


class TestSectionsFailIndependently(unittest.TestCase):
    """
    一塊壞掉不該讓整頁空白,但**必須說出來** ——
    回傳空資料而不講原因,看的人會以為「目前沒有訂單」。
    """

    def test_a_failing_section_reports_the_error(self):
        def explode():
            raise RuntimeError("資料庫掛了")

        with patch.object(transparency, "logger"):
            result = transparency._safe("x", explode, {"rows": []})

        self.assertIn("資料庫掛了", result["error"])
        self.assertEqual(result["rows"], [])

    def test_a_working_section_has_no_error_key(self):
        result = transparency._safe("x", lambda: {"rows": [1]}, {"rows": []})

        self.assertNotIn("error", result)


class TestOrdersEndpoint(unittest.TestCase):

    def _order(self, state, symbol="BTC/USDT"):
        from agmcis.core.models import Order

        return Order(
            client_order_id=f"coid-{state}", symbol=symbol,
            market_type="perpetual", side="buy", order_type="market",
            quantity=1.0, state=state,
        )

    def _store(self, unresolved=(), open_orders=()):
        store = MagicMock()
        store.unresolved.return_value = list(unresolved)
        store.open_orders.return_value = list(open_orders)
        return store

    def _run(self, store):
        from agmcis.execution import order_store

        with patch.object(order_store, "get_store", return_value=store):
            return transparency._orders()

    def test_a_filled_order_without_protection_counts_as_naked(self):
        """這個數字不該大於 0。大於 0 就是有部位沒有虧損上限。"""
        from agmcis.core.enums import OrderState

        result = self._run(self._store(
            open_orders=[self._order(OrderState.FILLED)],
        ))

        self.assertEqual(result["naked_count"], 1)
        self.assertTrue(result["open"][0]["is_naked"])

    def test_a_protected_order_is_not_naked(self):
        from agmcis.core.enums import OrderState

        result = self._run(self._store(
            open_orders=[self._order(OrderState.PROTECTED)],
        ))

        self.assertEqual(result["naked_count"], 0)

    def test_unknown_orders_are_flagged_as_needing_reconciliation(self):
        from agmcis.core.enums import OrderState

        result = self._run(self._store(
            unresolved=[self._order(OrderState.UNKNOWN)],
        ))

        self.assertEqual(result["unresolved_count"], 1)
        self.assertTrue(result["unresolved"][0]["needs_reconciliation"])

    def test_a_broken_store_reports_an_error_instead_of_an_empty_list(self):
        store = MagicMock()
        store.unresolved.side_effect = RuntimeError("讀不到")

        with patch.object(transparency, "logger"):
            result = self._run(store)

        self.assertIn("讀不到", result["error"])


class TestSummaryOnlyReportsWhatNeedsAttention(unittest.TestCase):

    def _summary(self, orders, calibration, reconciliation, review=None,
                 config=None):
        review = review if review is not None else {"verdict": "HEALTHY"}
        config = config if config is not None else {"risk_increase_count": 0}

        with patch.object(transparency, "_orders", return_value=orders), \
             patch.object(transparency, "_calibration", return_value=calibration), \
             patch.object(transparency, "_reconciliation",
                          return_value=reconciliation), \
             patch.object(transparency, "_self_review", return_value=review), \
             patch.object(transparency, "_config_changes", return_value=config):
            return transparency._summary()

    def test_a_healthy_system_produces_no_alerts(self):
        """
        一切正常時每個計數都是 0。那本身就是有意義的資訊 ——
        不是「還沒檢查」。
        """
        result = self._summary(
            {"naked_count": 0, "unresolved_count": 0},
            {"calibrated": True},
            {"critical_count": 0},
        )

        self.assertEqual(result["alerts"], [])

    def test_a_naked_position_is_critical(self):
        result = self._summary(
            {"naked_count": 1, "unresolved_count": 0},
            {"calibrated": True},
            {"critical_count": 0},
        )

        self.assertEqual(result["alerts"][0]["level"], "critical")
        self.assertIn("停損", result["alerts"][0]["message"])

    def test_unresolved_orders_are_a_warning(self):
        result = self._summary(
            {"naked_count": 0, "unresolved_count": 2},
            {"calibrated": True},
            {"critical_count": 0},
        )

        self.assertEqual(result["alerts"][0]["level"], "warning")

    def test_an_uncalibrated_system_says_so(self):
        """全部都是猜測值時,強平價與成本都只是估計。這必須講出來。"""
        result = self._summary(
            {"naked_count": 0, "unresolved_count": 0},
            {"calibrated": False},
            {"critical_count": 0},
        )

        self.assertTrue(any("校準" in a["message"] for a in result["alerts"]))

    def test_a_section_error_becomes_a_critical_alert(self):
        """
        某一塊讀不到資料時不可以只是安靜地少一塊 ——
        看的人會以為那裡沒有問題。
        """
        result = self._summary(
            {"naked_count": 0, "unresolved_count": 0, "error": "讀不到訂單"},
            {"calibrated": True},
            {"critical_count": 0},
        )

        self.assertTrue(any("讀不到訂單" in a["message"] for a in result["alerts"]))

    def test_a_losing_self_review_is_critical(self):
        """期望值為負時不該只是靜靜地列在報告裡。"""
        result = self._summary(
            {"naked_count": 0, "unresolved_count": 0},
            {"calibrated": True},
            {"critical_count": 0},
            review={"verdict": "LOSING", "headline": "期望值 -1.2 USDT/筆"},
        )

        self.assertEqual(result["alerts"][0]["level"], "critical")
        self.assertIn("LOSING", result["alerts"][0]["message"])

    def test_a_fragile_self_review_is_a_warning(self):
        """績效依賴少數幾筆極端獲利,那不是優勢 —— 但也還不是虧損。"""
        result = self._summary(
            {"naked_count": 0, "unresolved_count": 0},
            {"calibrated": True},
            {"critical_count": 0},
            review={"verdict": "FRAGILE", "headline": "扣掉最好的三筆就變負"},
        )

        self.assertEqual(result["alerts"][0]["level"], "warning")

    def test_not_enough_data_is_not_an_alert(self):
        """
        樣本不夠是常態,不是異常。把它做成警示會讓真正的警示被淹沒。
        """
        result = self._summary(
            {"naked_count": 0, "unresolved_count": 0},
            {"calibrated": True},
            {"critical_count": 0},
            review={"verdict": "NOT_ENOUGH_DATA", "headline": "只有 3 筆"},
        )

        self.assertEqual(result["alerts"], [])

    def test_a_loosened_risk_limit_is_surfaced(self):
        """「先放寬一下試試看」之後常常沒有人記得改回來。"""
        result = self._summary(
            {"naked_count": 0, "unresolved_count": 0},
            {"calibrated": True},
            {"critical_count": 0},
            config={"risk_increase_count": 2},
        )

        self.assertTrue(any("放寬" in a["message"] for a in result["alerts"]))

    def test_reconciliation_criticals_are_surfaced(self):
        result = self._summary(
            {"naked_count": 0, "unresolved_count": 0},
            {"calibrated": True},
            {"critical_count": 3},
        )

        self.assertTrue(any("對帳" in a["message"] for a in result["alerts"]))


class TestPageIsRegistered(unittest.TestCase):

    def _paths(self):
        import main

        main.app.openapi_schema = None
        return main.app.openapi()["paths"]

    def test_the_transparency_route_exists(self):
        self.assertIn("/transparency", self._paths())

    def test_the_api_router_is_mounted(self):
        """掛不上去的端點等於沒有寫。"""
        paths = self._paths()

        for endpoint in ("/api/agent_votes", "/api/orders", "/api/reconciliation",
                         "/api/costs", "/api/calibration",
                         "/api/transparency_summary"):
            with self.subTest(endpoint=endpoint):
                self.assertIn(endpoint, paths)


if __name__ == "__main__":
    unittest.main()
