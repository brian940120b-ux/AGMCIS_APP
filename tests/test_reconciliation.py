"""
對帳(Phase 13)。

三條原則,每一條都有測試:

  1. **狀態不明的訂單不可以重送,只能查。**
  2. **兩邊不一致時,交易所永遠是事實來源。**
  3. **對帳只更正本地紀錄,絕不下單。**

第 3 條最違反直覺。想「順手把多出來的倉位平掉」是很自然的念頭,
但對帳跑在排程裡、在資料不完整時也會跑 —— 一個會自動平倉的對帳程式,
在交易所 API 回傳空清單的那一刻會把整個帳戶清空。
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.core.enums import OrderState
from agmcis.core.models import Order
from agmcis.execution import reconciliation as recon
from agmcis.execution import state_machine as sm


def order(client_order_id="coid-1", symbol="BTC/USDT", state=OrderState.UNKNOWN):
    return Order(
        client_order_id=client_order_id, symbol=symbol,
        market_type="perpetual", side="buy", order_type="market",
        quantity=1.0, state=state,
    )


class FakeStore:
    def __init__(self, unresolved=None):
        self._unresolved = list(unresolved or [])
        self.saved = []
        self.events = []
        self.reconciled = []

    def unresolved(self):
        return list(self._unresolved)

    def save(self, order, trade_id=None, source="EXEC"):
        self.saved.append((order.client_order_id, order.state.value))

    def record_event(self, client_order_id, from_state, to_state, reason=None):
        self.events.append((client_order_id, to_state.value
                            if hasattr(to_state, "value") else to_state, reason))

    def mark_reconciled(self, client_order_id):
        self.reconciled.append(client_order_id)


def position(symbol="BTC/USDT", quantity=1.0, stoploss=97.0):
    return {"symbol": symbol, "quantity": quantity, "stoploss": stoploss,
            "signal": "做多", "entry_price": 100.0}


def build(store=None, fetch_order=None, fetch_positions=None, local=None):
    return recon.Reconciler(
        store=store or FakeStore(),
        fetch_order=fetch_order,
        fetch_positions=fetch_positions,
        local_positions=(lambda: list(local or [])),
    )


class TestReconciliationNeverTrades(unittest.TestCase):
    """對帳只更正本地紀錄,絕不下單。"""

    def test_the_module_imports_nothing_that_can_trade(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "agmcis", "execution", "reconciliation.py",
        )
        with open(path, encoding="utf-8") as handle:
            source = handle.read()

        for banned in ("create_paper_trade", "close_paper_trade", "create_order",
                       "close_position", "submit_entry", "ExecutionEngine"):
            with self.subTest(name=banned):
                self.assertNotIn(banned, source)

    def test_an_exchange_only_position_is_reported_not_closed(self):
        """
        沒有人在管的倉位是最危險的差異 —— 但對帳只回報,不動手。
        """
        report = build(
            fetch_positions=lambda: [position(symbol="GHOST/USDT")],
            local=[],
        ).run()

        kinds = [d.kind for d in report.discrepancies]
        self.assertIn(recon.EXCHANGE_ONLY_POSITION, kinds)
        self.assertTrue(report.critical)


class TestUnknownOrders(unittest.TestCase):

    def test_an_order_the_exchange_never_saw_is_resolved_as_rejected(self):
        """交易所查無此單 = 它從來沒被接受過。這是可以確定的答案。"""
        store = FakeStore([order()])
        report = build(store=store, fetch_order=lambda coid, symbol: None).run()

        self.assertEqual(store.saved[-1][1], "rejected")
        self.assertEqual(report.resolved, ["coid-1"])

    def test_an_order_the_exchange_filled_is_resolved_as_filled(self):
        store = FakeStore([order()])
        remote = {"state": "filled", "filled_quantity": 1.0,
                  "average_price": 100.5, "exchange_order_id": "X9"}

        report = build(store=store, fetch_order=lambda c, s: remote).run()

        self.assertEqual(store.saved[-1][1], "filled")
        self.assertIn("coid-1", store.reconciled)
        self.assertEqual(report.resolved, ["coid-1"])

    def test_without_a_query_source_the_order_stays_unknown(self):
        """查不到就是查不到。不可以猜,更不可以重送。"""
        store = FakeStore([order()])
        report = build(store=store, fetch_order=None).run()

        self.assertEqual(store.saved, [])
        kinds = [d.kind for d in report.discrepancies]
        self.assertIn(recon.ORDER_STILL_UNKNOWN, kinds)
        self.assertIn("不可以重送", report.discrepancies[0].detail)

    def test_a_failing_query_leaves_the_order_unknown(self):
        store = FakeStore([order()])

        def explode(coid, symbol):
            raise TimeoutError("查詢逾時")

        with patch.object(recon, "logger"):
            report = build(store=store, fetch_order=explode).run()

        self.assertEqual(store.saved, [])
        self.assertEqual(report.discrepancies[0].kind, recon.ORDER_STILL_UNKNOWN)

    def test_an_unrecognisable_remote_state_is_not_guessed(self):
        store = FakeStore([order()])
        remote = {"state": "什麼鬼狀態"}

        with patch.object(recon, "logger"):
            report = build(store=store, fetch_order=lambda c, s: remote).run()

        self.assertEqual(store.saved, [])
        self.assertEqual(report.discrepancies[0].kind, recon.ORDER_STILL_UNKNOWN)

    def test_a_contradictory_state_is_reported_not_forced(self):
        """
        本地 CLOSED、交易所說 FILLED —— 兩邊在邏輯上兜不起來。
        硬寫進去只會把矛盾藏起來。
        """
        store = FakeStore([order(state=OrderState.CLOSED)])

        with patch.object(recon, "logger"):
            report = build(
                store=store, fetch_order=lambda c, s: {"state": "filled"},
            ).run()

        self.assertEqual(store.saved, [])
        self.assertIn("兜不起來", report.discrepancies[0].detail)

    def test_a_resolved_order_may_be_acted_on_again(self):
        """對帳的意義就在這裡:解決之後這張單才重新可用。"""
        resolved = order(state=OrderState.UNKNOWN)
        sm.transition(resolved, OrderState.ACCEPTED, reason="對帳")

        self.assertTrue(sm.can_resubmit(resolved))


class TestPositionDrift(unittest.TestCase):

    def test_matching_positions_produce_no_discrepancy(self):
        report = build(
            fetch_positions=lambda: [position()], local=[position()],
        ).run()

        self.assertTrue(report.ok)

    def test_a_position_only_the_local_side_knows_about(self):
        report = build(fetch_positions=lambda: [], local=[position()]).run()

        kinds = [d.kind for d in report.discrepancies]
        self.assertIn(recon.LOCAL_ONLY_POSITION, kinds)

    def test_a_size_mismatch_beyond_tolerance_is_reported(self):
        report = build(
            fetch_positions=lambda: [position(quantity=0.5)],
            local=[position(quantity=1.0)],
        ).run()

        kinds = [d.kind for d in report.discrepancies]
        self.assertIn(recon.SIZE_MISMATCH, kinds)

    def test_a_tiny_size_difference_is_tolerated(self):
        """浮點與交易所的精度處理會有微小差距,那不是差異。"""
        report = build(
            fetch_positions=lambda: [position(quantity=1.0001)],
            local=[position(quantity=1.0)],
        ).run()

        self.assertTrue(report.ok)

    def test_an_unprotected_position_is_flagged_without_the_exchange(self):
        """沒有停損這件事不需要問交易所也看得出來。"""
        report = build(local=[position(stoploss=None)]).run()

        kinds = [d.kind for d in report.discrepancies]
        self.assertIn(recon.UNPROTECTED_POSITION, kinds)
        self.assertTrue(report.critical)

    def test_no_exchange_source_is_not_an_error(self):
        """模擬盤沒有交易所部位來源,那是常態不是錯誤。"""
        report = build(local=[position()]).run()

        self.assertEqual(report.errors, [])
        self.assertTrue(report.ok)


class TestFailuresAreReportedNotSwallowed(unittest.TestCase):

    def test_a_failing_local_read_becomes_an_error_not_a_clean_bill(self):
        """
        讀不到本地部位卻回報「一切正常」,比直接壞掉更危險。
        """
        reconciler = recon.Reconciler(
            store=FakeStore(),
            local_positions=MagicMock(side_effect=RuntimeError("資料庫掛了")),
        )

        with patch.object(recon, "logger"):
            report = reconciler.run()

        self.assertFalse(report.ok)
        self.assertTrue(report.errors)

    def test_a_failing_exchange_read_becomes_an_error(self):
        def explode():
            raise ConnectionError("斷線")

        with patch.object(recon, "logger"):
            report = build(fetch_positions=explode, local=[position()]).run()

        self.assertFalse(report.ok)
        self.assertTrue(report.errors)

    def test_critical_discrepancies_are_logged_at_critical(self):
        with patch.object(recon, "logger") as logger:
            build(local=[position(stoploss=None)]).run()

        logger.critical.assert_called()


class TestPaperBrokerSources(unittest.TestCase):

    def _broker(self, position=None):
        from agmcis.execution.broker import PaperBroker

        return PaperBroker(trading=MagicMock(),
                           positions=lambda symbol: position)

    def test_no_position_means_the_order_never_existed(self):
        self.assertIsNone(self._broker(None).fetch_order("coid", "BTC/USDT"))

    def test_a_position_with_a_stop_reports_protected(self):
        remote = self._broker({
            "id": 7, "entry_price": 100.0, "position_value": 300.0,
            "stoploss": 97.0,
        }).fetch_order("coid", "BTC/USDT")

        self.assertEqual(remote["state"], "protected")
        self.assertAlmostEqual(remote["filled_quantity"], 3.0, places=6)

    def test_a_position_without_a_stop_reports_filled_not_protected(self):
        """
        差別是有意義的:filled 但沒 protected 就是裸倉,
        對帳看到它才知道要處理。
        """
        remote = self._broker({
            "id": 7, "entry_price": 100.0, "position_value": 300.0,
            "stoploss": None,
        }).fetch_order("coid", "BTC/USDT")

        self.assertEqual(remote["state"], "filled")

    def test_paper_position_reconciliation_compares_data_with_itself(self):
        """
        這一條是把限制寫成測試:模擬盤的 fetch_positions 就是交易表,
        所以部位對帳永遠不會發現差異。真正的漂移只有實盤驗得出來。
        """
        from agmcis.execution.broker import PaperBroker

        source = inspect_source(PaperBroker.fetch_positions)
        self.assertIn("get_open_trades", source)


def inspect_source(func):
    import inspect
    return inspect.getsource(func)


class TestSchedulerRegistration(unittest.TestCase):

    def test_reconciliation_runs_on_a_schedule(self):
        """只在有人想起來的時候手動跑,等於沒有對帳。"""
        from agmcis.scheduling import runner

        names = [f.__name__ for f in runner.JOB_SETS[runner.JOB_SET_POSITION]]
        self.assertIn("_job_reconciliation", names)


if __name__ == "__main__":
    unittest.main()
