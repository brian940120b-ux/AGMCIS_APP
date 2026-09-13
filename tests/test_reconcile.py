"""
對帳(第十七條)。

═══ 這一組最重要的兩條 ═══
一、**「沒有樣本」不算「通過」。** 持倉是 0 檔的時候,持倉欄位的檢查
    沒有東西可以對照 —— 那時候回報 ok=True,就是在說謊。
    而那組欄位裡有 liquidationPrice。

二、**對帳只報告,不修正。** 自動拿交易所覆蓋帳本,差異就此消失,
    而造成差異的 bug 永遠不會被發現;自動拿帳本去補單,
    一個算錯的帳本會開始下真實的單。
"""
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from portfolio import reconcile as rec


def position(symbol="BTC-USDT", amt="0.5", **extra):
    row = {
        "symbol": symbol, "positionAmt": amt, "avgPrice": "60000",
        "liquidationPrice": "40000", "markPrice": "61000",
        "unrealizedProfit": "500", "initialMargin": "10000",
        "leverage": "3",
    }
    row.update(extra)
    return row


BALANCE = {"asset": "VST", "equity": "100000", "balance": "100000",
           "availableMargin": "100000", "usedMargin": "0",
           "unrealizedProfit": "0"}


class TestNoSampleIsNotAPass(unittest.TestCase):
    """⚠️ 這一組是整個檔案存在的第一個理由。"""

    def test_empty_positions_report_unverified_not_ok(self):
        report = rec.check_fields([], rec.POSITION_FIELDS, "持倉")

        self.assertFalse(report.checked)
        self.assertFalse(report.ok, "沒有樣本卻回報通過 —— 那是說謊")
        self.assertIn("沒有人驗證過", report.reason)

    def test_none_is_also_unverified(self):
        self.assertFalse(rec.check_fields(None, rec.POSITION_FIELDS, "x").ok)

    def test_a_reconciliation_with_no_positions_is_not_clean(self):
        """
        差異為零,但持倉欄位沒驗證過 —— 不算乾淨。
        那可能只是因為我們讀不懂交易所的回應。
        """
        result = rec.reconcile({}, BALANCE, [])

        self.assertEqual(result.differences, [])
        self.assertTrue(result.balance_fields.ok)
        self.assertFalse(result.position_fields.checked)
        self.assertFalse(result.clean)


class TestFieldShape(unittest.TestCase):

    def test_a_complete_position_passes(self):
        report = rec.check_fields([position()], rec.POSITION_FIELDS, "持倉")

        self.assertTrue(report.checked)
        self.assertTrue(report.ok)
        self.assertEqual(report.missing, [])

    def test_a_missing_critical_field_fails(self):
        row = position()
        del row["liquidationPrice"]

        report = rec.check_fields([row], rec.POSITION_FIELDS, "持倉")

        self.assertFalse(report.ok)
        self.assertIn("liquidationPrice", report.missing_critical)

    def test_a_missing_optional_field_is_reported_but_not_fatal(self):
        row = position()
        del row["leverage"]

        report = rec.check_fields([row], rec.POSITION_FIELDS, "持倉")

        self.assertTrue(report.ok)
        self.assertIn("leverage", report.missing)
        self.assertEqual(report.missing_critical, [])

    def test_an_alias_is_accepted_but_announced(self):
        """
        交易所改名字是一件要被看見的事。靜靜換掉,下次它再改就沒人知道。
        """
        row = position()
        del row["liquidationPrice"]
        row["liqPrice"] = "40000"

        report = rec.check_fields([row], rec.POSITION_FIELDS, "持倉")

        self.assertTrue(report.ok)
        self.assertEqual(report.via_alias["liquidationPrice"], "liqPrice")

    def test_the_nested_balance_shape_is_unwrapped(self):
        """BingX 的餘額回應把資料包在 balance 底下。"""
        report = rec.check_fields({"balance": BALANCE},
                                  rec.BALANCE_FIELDS, "餘額")

        self.assertTrue(report.ok)


class TestQuantityDiff(unittest.TestCase):

    def test_matching_books_produce_no_difference(self):
        self.assertEqual(
            rec.compare({"BTC-USDT": 0.5}, [position(amt="0.5")]), [])

    def test_a_position_only_we_have(self):
        diffs = rec.compare({"BTC-USDT": 0.5}, [])

        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0].kind, rec.ONLY_OURS)

    def test_a_position_only_the_exchange_has(self):
        """
        **這是最危險的一種。** 交易所有一個倉,而我方帳本不知道 ——
        風控算不到它,停損也不會照顧它。
        """
        diffs = rec.compare({}, [position()])

        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0].kind, rec.ONLY_THEIRS)

    def test_an_amount_mismatch(self):
        diffs = rec.compare({"BTC-USDT": 0.5}, [position(amt="0.3")])

        self.assertEqual(diffs[0].kind, rec.AMOUNT)
        self.assertAlmostEqual(diffs[0].ours, 0.5)
        self.assertAlmostEqual(diffs[0].theirs, 0.3)

    def test_floating_point_noise_is_not_a_difference(self):
        self.assertEqual(
            rec.compare({"BTC-USDT": 0.1 + 0.2}, [position(amt="0.3")]), [])

    def test_a_short_is_negative(self):
        diffs = rec.compare({"BTC-USDT": -0.5},
                            [position(amt="0.5", positionSide="SHORT")])

        self.assertEqual(diffs, [], "positionSide=SHORT 要被讀成負數")

    def test_an_unreadable_amount_is_not_treated_as_zero(self):
        """
        回 0 會讓「這個欄位我讀不懂」看起來像「這個倉是空的」,
        然後對帳會安靜地說一切正常(第九十四條)。
        """
        diffs = rec.compare({}, [position(positionAmt="不是數字")])

        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0].kind, rec.ONLY_THEIRS)
        self.assertIn("讀不出數量", diffs[0].detail)

    def test_a_row_without_a_symbol_is_flagged_not_dropped(self):
        diffs = rec.compare({}, [{"positionAmt": "1"}])

        self.assertEqual(len(diffs), 1)

    def test_two_rows_for_one_symbol_are_summed(self):
        """雙向持倉模式:同一個幣可能有多單與空單兩列。"""
        diffs = rec.compare(
            {"BTC-USDT": 0.2},
            [position(amt="0.5"),
             position(amt="0.3", positionSide="SHORT")])

        self.assertEqual(diffs, [])


class TestItOnlyReports(unittest.TestCase):
    """
    ⚠️ 第二個理由。一個會自己修帳的對帳層是災難。
    """

    SOURCES = ("portfolio/reconcile.py", "scripts/reconcile.py")

    WRITES = {"write_json_atomic", "save", "_append", "write_text",
              "write_bytes", "unlink", "submit", "create_order", "post"}

    def test_no_write_call_anywhere(self):
        for rel in self.SOURCES:
            path = ROOT / rel
            tree = ast.parse(path.read_text(encoding="utf-8"),
                             filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = (node.func.attr if isinstance(node.func, ast.Attribute)
                        else getattr(node.func, "id", None))
                with self.subTest(file=rel, line=node.lineno):
                    self.assertNotIn(
                        name, self.WRITES,
                        f"{rel}:{node.lineno} 呼叫了 {name}() —— "
                        "對帳只報告,不修正任何一邊")

    def test_compare_does_not_mutate_its_inputs(self):
        ours = {"BTC-USDT": 0.5}
        theirs = [position(amt="0.3")]
        before_ours, before_theirs = dict(ours), [dict(theirs[0])]

        rec.compare(ours, theirs)

        self.assertEqual(ours, before_ours)
        self.assertEqual(theirs, before_theirs)

    def test_the_tolerance_is_below_exchange_precision(self):
        """
        把容差放大來讓紅字消失,是這一層最容易被破壞的方式。
        交易所的數量精度最細到小數 8 位。
        """
        self.assertLessEqual(rec.TOLERANCE, 1e-8)


if __name__ == "__main__":
    unittest.main()
