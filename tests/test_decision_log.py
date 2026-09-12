"""
決策紀錄與可解釋性(Master Prompt 第六十九 / 七十 / 七十一節)。

在這一層出現以前,系統可以回答「我現在為什麼要開這一單」,
但答不出「三週前那一單為什麼開」。決策只寫在 log 與 API 回應裡,
而 log 會輪替。

這組測試最在意三件事:
  一、寫紀錄失敗**不能**擋住交易(這是觀測,不是控制)。
  二、失敗也不能安靜(安靜失效會讓人以為「沒紀錄 = 沒發生」)。
  三、解釋裡的缺漏要寫出來,不能省略掉。
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.core.enums import Direction
from agmcis.review import decision_log


class FakeDeliberation:
    def __init__(self, symbol="BTC/USDT", direction=Direction.LONG,
                 intent=None, votes=None, blocked_reason=None, confidence=70.0):
        self.symbol = symbol
        self.direction = direction
        self.intent = intent
        self.votes = votes or {"trend": "LONG", "momentum": "LONG", "news": "ABSTAIN"}
        self.blocked_reason = blocked_reason
        self.confidence = confidence


class FakeIntent:
    market_type = "perpetual"
    score = 82.0
    confidence = 76.0
    market_regime = "BULL"


class FakeDecision:
    def __init__(self, approved=True, reason=None, blockers=None, warnings=None):
        self._payload = {
            "approved": approved,
            "size_usdt": 200.0 if approved else None,
            "leverage": 3.0 if approved else None,
            "reason": reason,
            "blockers": list(blockers or []),
            "warnings": list(warnings or []),
        }

    def to_dict(self):
        return dict(self._payload)


class TestBuildingARecord(unittest.TestCase):

    def test_a_wait_is_recorded_too(self):
        """
        「為什麼沒開」跟「為什麼開」一樣重要,而且頻繁得多。
        系統連續三天沒交易的時候,唯一能回答「壞了還是在等」的就是這個。
        """
        record = decision_log.from_deliberation(
            FakeDeliberation(blocked_reason="所有策略都觀望"),
            outcome=decision_log.WAIT,
        )

        self.assertEqual(record.outcome, decision_log.WAIT)
        self.assertEqual(record.reason, "所有策略都觀望")
        self.assertTrue(record.agent_votes)

    def test_an_opened_decision_carries_the_risk_verdict(self):
        record = decision_log.from_deliberation(
            FakeDeliberation(intent=FakeIntent()),
            outcome=decision_log.OPENED,
            decision=FakeDecision(approved=True),
        )

        self.assertEqual(record.risk_decision["size_usdt"], 200.0)
        self.assertEqual(record.score, 82.0)

    def test_every_record_gets_a_unique_sortable_id(self):
        first = decision_log.Decision(symbol="BTC/USDT", outcome="WAIT")
        second = decision_log.Decision(symbol="BTC/USDT", outcome="WAIT")

        self.assertNotEqual(first.decision_id, second.decision_id)
        # 時間前綴,所以字串排序就是時間排序
        self.assertTrue(first.decision_id[:4].isdigit())

    def test_the_id_survives_a_symbol_with_slashes(self):
        record = decision_log.Decision(symbol="BTC/USDT:USDT", outcome="WAIT")

        self.assertNotIn("/", record.decision_id)
        self.assertNotIn(":", record.decision_id)

    def test_a_missing_report_does_not_break_the_record(self):
        """管線更早被擋下時本來就沒有報告,而那種情況同樣要留紀錄。"""
        record = decision_log.from_deliberation(
            FakeDeliberation(), report=None, outcome=decision_log.BLOCKED,
        )

        self.assertEqual(record.symbol, "BTC/USDT")


class TestWritingNeverBlocksTrading(unittest.TestCase):
    """
    這一層是觀測,不是控制。資料庫掛掉時,「因為寫不了紀錄所以不能下單」
    是錯誤的取捨方向 —— 風控該擋的東西風控會擋。
    """

    def test_a_failing_writer_does_not_raise(self):
        def explode(payload):
            raise RuntimeError("資料庫掛了")

        record = decision_log.Decision(symbol="BTC/USDT", outcome="WAIT")

        with patch.object(decision_log, "logger"):
            self.assertIsNone(decision_log.record(record, writer=explode))

    def test_a_failing_writer_is_logged_loudly(self):
        """安靜失效會讓人以為「沒有紀錄 = 沒有發生過」。"""
        def explode(payload):
            raise RuntimeError("資料庫掛了")

        with patch.object(decision_log, "logger") as logger:
            decision_log.record(
                decision_log.Decision(symbol="BTC/USDT", outcome="WAIT"),
                writer=explode,
            )

        logger.exception.assert_called()

    def test_a_successful_write_returns_the_id(self):
        written = []
        record = decision_log.Decision(symbol="BTC/USDT", outcome="WAIT")

        result = decision_log.record(record, writer=written.append)

        self.assertEqual(result, record.decision_id)
        self.assertEqual(written[0]["symbol"], "BTC/USDT")

    def test_risk_events_also_never_raise(self):
        def explode(payload):
            raise RuntimeError("掛了")

        with patch.object(decision_log, "logger"):
            self.assertFalse(
                decision_log.record_risk_event("X", writer=explode)
            )

    def test_audit_never_raises(self):
        def explode(payload):
            raise RuntimeError("掛了")

        with patch.object(decision_log, "logger"):
            self.assertFalse(decision_log.audit("MODE_CHANGE", writer=explode))

    def test_audit_records_who_and_what_changed(self):
        written = []
        decision_log.audit(
            "MODE_CHANGE", actor="env", target="TRADING_MODE",
            before="paper", after="live", writer=written.append,
        )

        self.assertEqual(written[0]["action"], "MODE_CHANGE")
        self.assertEqual(written[0]["before_value"], "paper")
        self.assertEqual(written[0]["after_value"], "live")

    def test_the_actor_can_honestly_be_env(self):
        """
        設定來自環境變數,環境變數沒有作者。
        誠實地寫 'env' 比編一個假的使用者名稱好。
        """
        written = []
        decision_log.audit("RISK_CHANGE", actor="env", writer=written.append)

        self.assertEqual(written[0]["actor"], "env")


class TestExplainingATrade(unittest.TestCase):

    def _row(self, **overrides):
        row = {
            "decision_id": "20260101T000000-BTCUSDT-abc",
            "symbol": "BTC/USDT",
            "direction": "做多",
            "outcome": "OPENED",
            "score": 82.0,
            "confidence": 76.0,
            "market_regime": "BULL",
            "volatility": "NORMAL",
            "created_at": "2026-01-01 00:00:00",
            "agent_votes": {"trend": "LONG", "momentum": "LONG", "news": "ABSTAIN"},
            "score_breakdown": {
                "components": {"trend_alignment": 15, "momentum": 10},
                "missing": ["order_book"],
            },
            "risk_decision": {
                "approved": True, "size_usdt": 200.0, "leverage": 3.0,
                "warnings": ["ETH/USDT 因為算不出相關係數而被當成相關"],
            },
            "reason": None,
        }
        row.update(overrides)
        return row

    def test_it_answers_why_in_plain_language(self):
        text = decision_log.explain(self._row()).as_text()

        self.assertIn("BTC/USDT", text)
        self.assertIn("做多", text)
        self.assertIn("BULL", text)

    def test_the_agent_votes_are_grouped_by_verdict(self):
        text = decision_log.explain(self._row()).as_text()

        self.assertIn("LONG", text)
        self.assertIn("ABSTAIN", text)

    def test_missing_score_components_are_named(self):
        """
        缺了哪一項評分跟拿了幾分一樣重要 —— 分母已經被扣掉了,
        不說出來的話那個分數會被高估。
        """
        text = decision_log.explain(self._row()).as_text()

        self.assertIn("order_book", text)

    def test_risk_warnings_are_shown_even_on_an_approved_trade(self):
        text = decision_log.explain(self._row()).as_text()

        self.assertIn("相關", text)

    def test_a_rejected_decision_says_why(self):
        row = self._row(
            outcome="REJECTED_BY_RISK",
            risk_decision={
                "approved": False, "reason": "相關群一起停損會虧 3.4% 權益",
                "blockers": ["MAX_CORRELATED_RISK"],
            },
        )
        text = decision_log.explain(row).as_text()

        self.assertIn("拒絕", text)
        self.assertIn("3.4%", text)

    def test_gaps_are_stated_not_omitted(self):
        """
        省略會讓讀的人以為那一項沒有意見,但實際上是我們沒存。
        """
        row = self._row(agent_votes=None, score_breakdown=None, risk_decision=None)
        text = decision_log.explain(row).as_text()

        self.assertEqual(text.count("沒有紀錄"), 3)

    def test_json_strings_are_parsed(self):
        """有些驅動會把 JSONB 讀成字串。解釋不能因此變成一團 JSON。"""
        import json

        row = self._row(agent_votes=json.dumps({"trend": "LONG"}))
        text = decision_log.explain(row).as_text()

        self.assertIn("LONG: trend", text)

    def test_a_missing_record_says_so_rather_than_inventing_one(self):
        result = decision_log.explain(None)

        self.assertFalse(result.found)
        self.assertIn("找不到", result.as_text())

    def test_a_failing_lookup_is_reported_not_swallowed(self):
        def explode(trade_id):
            raise RuntimeError("查詢失敗")

        with patch.object(decision_log, "logger"):
            result = decision_log.explain_trade(7, fetch=explode)

        self.assertFalse(result.found)
        self.assertIn("查詢失敗", result.as_text())


class TestTheRecordIsWrittenBeforeTheOrder(unittest.TestCase):
    """
    送單當下當機的話,那筆決策不能跟著消失 ——
    事後要能查到「系統當時打算做什麼」。
    """

    def test_auto_trader_records_before_it_executes(self):
        import inspect

        import auto_trader

        source = inspect.getsource(auto_trader.run_auto_trader)
        record_at = source.index("decision_log.record(pending)")
        execute_at = source.index("execute(decision)")

        self.assertLess(record_at, execute_at)

    def test_auto_trader_records_rejections_too(self):
        import inspect

        import auto_trader

        source = inspect.getsource(auto_trader.run_auto_trader)
        self.assertIn("REJECTED_BY_RISK", source)
        self.assertIn("decision_log.WAIT", source)


if __name__ == "__main__":
    unittest.main()
