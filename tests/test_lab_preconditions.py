"""
研究腳本的前置條件(第二 / 九十九節)。

回測會扣手續費、算資金費率、判斷強平。那些數字沒校準的時候是**猜的**,
而回測照樣會印出 PASS / REJECT —— 報告上看不出來輸入是猜的。

第二節禁止忽略費用,也禁止在沒驗證的情況下宣稱結果。一份用猜測費率
算出來的 PASS 同時踩到兩條。

這一組驗兩件事:該擋的時候擋得住,以及擋不住的時候**章蓋得上**。
第二件比第一件重要 —— 擋得住的東西總有一天會被繞過,
蓋在報告上的章會跟著報告一起被讀到。
"""
import ast
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agmcis.lab import preconditions


def report(**overrides):
    base = {
        "calibrated": True, "stale": False, "testnet": False,
        "captured_at": "2026-09-01T00:00:00+00:00", "age_days": 2,
    }
    base.update(overrides)
    return base


def with_report(value):
    """把 specs 的校準報告換掉。value 是 dict 或要拋的例外。"""
    store = type("Store", (), {})()

    def calibration_report(symbols):
        if isinstance(value, Exception):
            raise value
        return value

    store.calibration_report = calibration_report
    return patch("agmcis.exchange.specs.get_store", lambda: store)


class TestCheck(unittest.TestCase):

    def test_a_fresh_snapshot_is_calibrated(self):
        with with_report(report()):
            quality = preconditions.check(["BTC/USDT"])

        self.assertTrue(quality.calibrated)
        self.assertEqual(quality.status, preconditions.OK)

    def test_a_missing_snapshot_is_not_calibrated(self):
        with with_report(report(calibrated=False)):
            quality = preconditions.check(["BTC/USDT"])

        self.assertFalse(quality.calibrated)
        self.assertEqual(quality.status, preconditions.MISSING)
        self.assertIn("verify_bingx", quality.detail)

    def test_a_stale_snapshot_is_not_calibrated(self):
        """費率與保證金分層會變。舊的快照算出來的強平價不準。"""
        with with_report(report(stale=True, age_days=99)):
            quality = preconditions.check(["BTC/USDT"])

        self.assertFalse(quality.calibrated)
        self.assertEqual(quality.status, preconditions.STALE)

    def test_a_testnet_snapshot_is_not_calibrated(self):
        """測試網的費率不一定等於正式環境。"""
        with with_report(report(testnet=True)):
            quality = preconditions.check(["BTC/USDT"])

        self.assertFalse(quality.calibrated)
        self.assertEqual(quality.status, preconditions.TESTNET)

    def test_an_unreadable_status_is_not_calibrated(self):
        """讀不到不等於沒問題。"""
        with with_report(RuntimeError("資料庫連不上")):
            quality = preconditions.check(["BTC/USDT"])

        self.assertFalse(quality.calibrated)
        self.assertEqual(quality.status, preconditions.UNKNOWN)
        self.assertIn("RuntimeError", quality.detail)


class TestRequire(unittest.TestCase):

    def run_require(self, value, allow=False):
        lines = []
        with with_report(value):
            quality = preconditions.require(
                ["BTC/USDT"], allow_uncalibrated=allow, writer=lines.append,
            )
        return quality, "\n".join(lines)

    def test_a_calibrated_snapshot_passes(self):
        quality, _ = self.run_require(report())

        self.assertIsNotNone(quality)
        self.assertTrue(quality.calibrated)

    def test_an_uncalibrated_snapshot_stops_the_run(self):
        quality, printed = self.run_require(report(calibrated=False))

        self.assertIsNone(quality, "沒校準就不該讓它跑")
        self.assertIn("verify_bingx", printed)

    def test_the_refusal_says_why_it_matters(self):
        """
        一個只說「不行」的守門員會被當成障礙繞過去。
        說出「跑出來的 PASS 不算數」才會讓人真的去校準。
        """
        _, printed = self.run_require(report(calibrated=False))

        self.assertIn("第二節", printed)
        self.assertIn("--allow-uncalibrated", printed)

    def test_the_override_lets_it_run_but_keeps_the_stamp(self):
        quality, printed = self.run_require(report(calibrated=False), allow=True)

        self.assertIsNotNone(quality)
        self.assertFalse(quality.calibrated)
        self.assertFalse(quality.to_dict()["trustworthy"])
        self.assertIn("trustworthy=false", printed)

    def test_every_uncalibrated_state_is_refused(self):
        for overrides in (
            {"calibrated": False},
            {"stale": True},
            {"testnet": True},
        ):
            with self.subTest(**overrides):
                quality, _ = self.run_require(report(**overrides))
                self.assertIsNone(quality)


class TestTheStamp(unittest.TestCase):

    def test_a_calibrated_report_is_marked_trustworthy(self):
        with with_report(report()):
            stamp = preconditions.check(["BTC/USDT"]).to_dict()

        self.assertTrue(stamp["trustworthy"])

    def test_an_uncalibrated_report_is_marked_untrustworthy(self):
        with with_report(report(calibrated=False)):
            stamp = preconditions.check(["BTC/USDT"]).to_dict()

        self.assertFalse(stamp["trustworthy"])
        self.assertTrue(stamp["headline"])

    def test_the_stamp_key_is_the_same_for_both_scripts(self):
        """讀報告的人不該需要記兩個欄位名。"""
        self.assertEqual(preconditions.STAMP_KEY, "input_quality")


class TestBothScriptsUseIt(unittest.TestCase):
    """
    寫好一個守門員卻只有一支腳本用它,等於另一支沒有守門員。
    用 AST 檢查而不是字串搜尋 —— 註解裡提到不算數。
    """

    SCRIPTS = ("scripts/run_strategy_lab.py", "scripts/tune_exits.py")

    def tree(self, relative):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            relative,
        )
        with open(path, encoding="utf-8") as handle:
            return ast.parse(handle.read(), filename=path)

    def test_each_script_calls_require(self):
        for relative in self.SCRIPTS:
            with self.subTest(script=relative):
                called = {
                    node.func.attr
                    for node in ast.walk(self.tree(relative))
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                }
                self.assertIn("require", called)

    def test_each_script_stamps_its_report(self):
        for relative in self.SCRIPTS:
            with self.subTest(script=relative):
                names = {
                    node.attr
                    for node in ast.walk(self.tree(relative))
                    if isinstance(node, ast.Attribute)
                }
                self.assertIn("STAMP_KEY", names)

    def test_each_script_offers_the_override(self):
        for relative in self.SCRIPTS:
            with self.subTest(script=relative):
                literals = {
                    node.value
                    for node in ast.walk(self.tree(relative))
                    if isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                }
                self.assertIn("--allow-uncalibrated", literals)


if __name__ == "__main__":
    unittest.main()
