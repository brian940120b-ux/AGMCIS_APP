"""
設定變更稽核。

風控參數是這個系統裡最容易被「先放寬一下試試看」的東西,
而放寬之後常常沒有人記得改回來。
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.config import audit


def auditor():
    directory = tempfile.mkdtemp()
    return audit.ConfigAuditor(
        snapshot_file=os.path.join(directory, "snapshot.json"),
        audit_file=os.path.join(directory, "audit.log"),
    )


class TestChangeClassification(unittest.TestCase):

    def test_raising_the_leverage_cap_is_a_risk_increase(self):
        change = audit.compare({"MAX_LEVERAGE": 5}, {"MAX_LEVERAGE": 20})[0]

        self.assertEqual(change.kind, audit.RISK_INCREASED)
        self.assertTrue(change.is_risk_increase)

    def test_lowering_the_leverage_cap_is_a_risk_decrease(self):
        change = audit.compare({"MAX_LEVERAGE": 20}, {"MAX_LEVERAGE": 5})[0]

        self.assertEqual(change.kind, audit.RISK_DECREASED)

    def test_lowering_the_assumed_fee_is_a_risk_increase(self):
        """
        調低成本假設會讓模擬績效變好看。那是一種自欺,不是優化。
        """
        change = audit.compare(
            {"PAPER_TAKER_FEE": 0.0005}, {"PAPER_TAKER_FEE": 0.0001},
        )[0]

        self.assertEqual(change.kind, audit.RISK_INCREASED)

    def test_lowering_the_assumed_maintenance_margin_is_a_risk_increase(self):
        """維持保證金率調低 = 強平價看起來變遠 = 風險被低估。"""
        change = audit.compare(
            {"PAPER_MAINTENANCE_MARGIN_RATIO": 0.1},
            {"PAPER_MAINTENANCE_MARGIN_RATIO": 0.004},
        )[0]

        self.assertEqual(change.kind, audit.RISK_INCREASED)

    def test_a_non_numeric_change_is_just_a_change(self):
        change = audit.compare(
            {"TRADING_MODE": "paper"}, {"TRADING_MODE": "test"},
        )[0]

        self.assertEqual(change.kind, audit.CHANGED)

    def test_unchanged_settings_produce_nothing(self):
        self.assertEqual(audit.compare({"MAX_LEVERAGE": 5}, {"MAX_LEVERAGE": 5}), [])

    def test_a_key_dropped_from_the_watch_list_is_still_recorded(self):
        """監控清單被改過本身就值得記一筆。"""
        changes = audit.compare({"REMOVED_KEY": 1}, {})

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].key, "REMOVED_KEY")


class TestAuditRun(unittest.TestCase):

    def test_the_first_run_only_creates_a_baseline(self):
        result = auditor().run()

        self.assertTrue(result.first_run)
        self.assertEqual(result.changes, [])

    def test_an_unchanged_second_run_reports_nothing(self):
        instance = auditor()
        instance.run()

        self.assertEqual(instance.run().changes, [])

    def test_a_changed_setting_is_detected_and_recorded(self):
        instance = auditor()
        instance.run()

        with patch.object(audit.settings, "MAX_LEVERAGE", 99):
            result = instance.run()

        self.assertEqual(len(result.risk_increases), 1)
        self.assertEqual(result.risk_increases[0].key, "MAX_LEVERAGE")

        records = instance.read_audit()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["risk_increase_count"], 1)

    def test_a_change_is_only_reported_once(self):
        """第二次執行時新值已經是基準,不該一直重複回報同一筆。"""
        instance = auditor()
        instance.run()

        with patch.object(audit.settings, "MAX_LEVERAGE", 99):
            instance.run()
            second = instance.run()

        self.assertEqual(second.changes, [])

    def test_a_risk_increase_is_logged_as_a_warning(self):
        instance = auditor()
        instance.run()

        with patch.object(audit.settings, "MAX_LEVERAGE", 99), \
             patch.object(audit, "logger") as logger:
            instance.run()

        logger.warning.assert_called()

    def test_nothing_is_written_to_the_audit_log_when_nothing_changed(self):
        """沒有變更就沒有紀錄。每小時寫一筆「沒事」會把真正的變更淹沒。"""
        instance = auditor()
        instance.run()
        instance.run()

        self.assertEqual(instance.read_audit(), [])


class TestBrokenSnapshotIsNotSilentlyReset(unittest.TestCase):

    def test_an_unreadable_snapshot_does_not_become_a_fresh_baseline(self):
        """
        把壞掉的快照當成「沒有快照」,會讓這一次的所有變更被當成
        首次執行而消失 —— 那正是最需要看到變更的時候。
        """
        instance = auditor()
        instance.snapshot_file.write_text("這不是 JSON", encoding="utf-8")

        with patch.object(audit, "logger") as logger:
            result = instance.run()

        self.assertFalse(result.first_run)
        self.assertEqual(result.changes, [])
        logger.error.assert_called()

        # 壞掉的快照沒有被覆蓋 —— 問題還在,看得見
        self.assertEqual(
            instance.snapshot_file.read_text(encoding="utf-8"), "這不是 JSON",
        )


class TestWhatItDoesNotClaim(unittest.TestCase):

    def test_the_record_never_names_a_user(self):
        """
        設定來自環境變數,環境變數沒有作者。
        猜一個使用者名稱填進稽核紀錄比留白更糟。
        """
        instance = auditor()
        instance.run()

        with patch.object(audit.settings, "MAX_LEVERAGE", 99):
            instance.run()

        record = instance.read_audit()[0]

        for banned in ("user", "changed_by", "author", "operator"):
            with self.subTest(field=banned):
                self.assertNotIn(banned, record)

    def test_the_module_says_so_in_words(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "agmcis", "config", "audit.py",
        )
        with open(path, encoding="utf-8") as handle:
            source = handle.read()

        self.assertIn("做不到", source)
        self.assertIn("環境變數沒有作者", source)


class TestWatchList(unittest.TestCase):

    def test_the_leverage_cap_is_watched(self):
        self.assertIn("MAX_LEVERAGE", audit.WATCHED)

    def test_the_cost_assumptions_are_watched(self):
        for key in ("PAPER_TAKER_FEE", "PAPER_SLIPPAGE_PCT",
                    "PAPER_MAINTENANCE_MARGIN_RATIO"):
            with self.subTest(key=key):
                self.assertIn(key, audit.WATCHED)

    def test_a_snapshot_covers_every_watched_key(self):
        """取不到的鍵要留 None,不是跳過 —— 跳過會讓它看起來沒被監控。"""
        current = audit.snapshot()

        self.assertEqual(set(current), set(audit.WATCHED))

    def test_the_scheduler_runs_it(self):
        from agmcis.scheduling import runner

        names = [f.__name__ for f in runner.JOB_SETS[runner.JOB_SET_POSITION]]
        self.assertIn("_job_config_audit", names)


if __name__ == "__main__":
    unittest.main()
