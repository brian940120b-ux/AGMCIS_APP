"""
完整交易日誌(Master Prompt 第四十一節)。

系統一直有存 27 個欄位裡的大部分,但沒有一個地方把它們組成一筆
完整的紀錄。這個模組拼起來,而且**明確標出拼不起來的部分**。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.review import journal


def trade(**overrides):
    base = {
        "id": 7,
        "symbol": "BTC/USDT",
        "signal": "做多",
        "strategy": "trend_following",
        "status": "CLOSED",
        "entry_price": 100.0,
        "requested_entry_price": 99.9,
        "exit_price": 110.0,
        "stoploss": 98.0,
        "original_stoploss": 95.0,
        "takeprofit": 115.0,
        "leverage": 3.0,
        "size_usdt": 1000.0,
        "position_value": 3000.0,
        "original_position_value": 3000.0,
        "entry_fee": 1.5,
        "exit_fee": 1.5,
        "funding_usdt": 0.4,
        "pnl_usdt": 297.0,
        "pnl_pct": 29.7,
        "confidence": 76.0,
        "market_regime": "BULL",
        "agent_votes": {"trend": "LONG"},
        "close_reason": "自動止盈",
        "opened_at": "2026-01-01 00:00:00",
        "closed_at": "2026-01-02 06:00:00",
        "realized_partial_usdt": 0.0,
    }
    base.update(overrides)
    return base


class TestDerivedFields(unittest.TestCase):
    """三個算出來而不是存下來的欄位。存衍生值遲早會與來源不一致。"""

    def test_slippage_is_the_gap_between_seen_and_filled(self):
        entry = journal.build(trade())

        self.assertAlmostEqual(entry.slippage_pct, 0.100100, places=5)

    def test_slippage_is_none_when_the_requested_price_was_not_recorded(self):
        """
        回 0 會讓「沒有記錄滑點」與「滑點是 0」變成同一件事。
        """
        entry = journal.build(trade(requested_entry_price=None))

        self.assertIsNone(entry.slippage_pct)

    def test_the_r_multiple_uses_the_original_stop(self):
        """
        停損被拉到成本價之後,用現在的停損算會讓分母趨近 0,
        R 倍數會爆掉 —— 而那是計算方式造成的,不是策略變好了。
        """
        entry = journal.build(trade())

        # 原始風險 = |100 - 95| / 100 x 3000 = 150;297 / 150 ≈ 1.98
        self.assertAlmostEqual(entry.r_multiple, 1.98, places=2)

    def test_the_r_multiple_would_be_wrong_with_the_moved_stop(self):
        """前提檢查:兩者確實會算出不同的數字。"""
        moved = journal.build(trade(original_stoploss=None))

        self.assertNotAlmostEqual(moved.r_multiple, 1.98, places=2)

    def test_duration_comes_from_the_timestamps(self):
        entry = journal.build(trade())

        self.assertAlmostEqual(entry.duration_hours, 30.0, places=3)

    def test_an_open_trade_has_no_duration(self):
        entry = journal.build(trade(closed_at=None))

        self.assertIsNone(entry.duration_hours)

    def test_unparseable_timestamps_give_none_not_zero(self):
        entry = journal.build(trade(closed_at="上禮拜"))

        self.assertIsNone(entry.duration_hours)


class TestMissingFieldsAreNamed(unittest.TestCase):
    """
    填 0 或「未知」會讓一批不能比較的資料混進統計。
    """

    def test_a_complete_trade_scores_high(self):
        entry = journal.build(
            trade(), decision={"score": 82.0, "market_type": "perpetual",
                               "reason": "趨勢確立"},
        )

        self.assertGreater(entry.completeness, 90)

    def test_an_old_trade_without_attribution_is_marked_incomplete(self):
        entry = journal.build(trade(
            agent_votes=None, market_regime=None, strategy=None,
            entry_fee=None, exit_fee=None, funding_usdt=None,
        ))

        self.assertLess(entry.completeness, 90)
        self.assertIn("agent_votes", entry.missing)
        self.assertIn("market_regime", entry.missing)

    def test_fees_are_none_rather_than_zero_when_not_recorded(self):
        """
        0 手續費與「沒有記錄手續費」是兩件事。Phase 10 之前的交易
        完全不含成本,混在一起平均會讓成本分析永遠答不出來。
        """
        entry = journal.build(trade(entry_fee=None, exit_fee=None))

        self.assertIsNone(entry.fees)

    def test_the_signal_score_comes_from_the_decision_record(self):
        entry = journal.build(trade(), decision={"score": 82.0})

        self.assertEqual(entry.signal_score, 82.0)

    def test_no_decision_record_means_no_score(self):
        entry = journal.build(trade())

        self.assertIsNone(entry.signal_score)
        self.assertIn("signal_score", entry.missing)


class TestBuildMany(unittest.TestCase):

    def test_decisions_are_matched_by_trade_id(self):
        entries = journal.build_many(
            [trade(id=1), trade(id=2)],
            decisions=[{"trade_id": 2, "score": 90.0}],
        )

        self.assertIsNone(entries[0].signal_score)
        self.assertEqual(entries[1].signal_score, 90.0)

    def test_an_unmatched_trade_does_not_disappear(self):
        """
        讓它消失會讓「有多少筆交易」這個數字變得不可信。
        """
        entries = journal.build_many([trade(id=1)], decisions=[])

        self.assertEqual(len(entries), 1)

    def test_partial_exits_are_attached(self):
        entries = journal.build_many(
            [trade(id=1)],
            exits=[{"trade_id": 1, "stage": 1, "fraction": 0.3}],
        )

        self.assertEqual(len(entries[0].partials), 1)


class TestSummary(unittest.TestCase):
    """
    這不是績效報告 —— 它報告的是資料有多完整。在拿一批
    completeness 60% 的紀錄去算勝率之前,要先知道那件事。
    """

    def test_it_reports_the_average_completeness(self):
        entries = journal.build_many([trade(id=1), trade(id=2)])
        summary = journal.summarise(entries)

        self.assertEqual(summary["count"], 2)
        self.assertGreater(summary["avg_completeness"], 0)

    def test_it_names_the_commonly_missing_fields(self):
        entries = journal.build_many([
            trade(id=1, agent_votes=None),
            trade(id=2, agent_votes=None),
        ])
        summary = journal.summarise(entries)

        names = [item["field"] for item in summary["commonly_missing"]]
        self.assertIn("agent_votes", names)

    def test_an_empty_batch_is_not_an_error(self):
        summary = journal.summarise([])

        self.assertEqual(summary["count"], 0)


if __name__ == "__main__":
    unittest.main()
