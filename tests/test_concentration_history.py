"""
集中度歷史分布腳本 —— 它決定一條 Risk Limit,所以它自己要對。

═══ 兩條真正重要的 ═══
  · 建議值必須在歷史最大值**之上**。低於歷史最大值的門檻,
    等於宣稱回測裡某幾天的決定是錯的 —— 而那需要重新回測。
  · 它只能讀,不能寫。一個會動到帳本的「分析腳本」是災難。
"""
import ast
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import concentration_history as ch


class TestThePercentileIsRight(unittest.TestCase):

    def test_it_matches_hand_computed_values(self):
        values = [1, 2, 3, 4, 5]
        self.assertEqual(ch.percentile(values, 0.0), 1)
        self.assertEqual(ch.percentile(values, 0.5), 3)
        self.assertEqual(ch.percentile(values, 1.0), 5)

    def test_it_interpolates(self):
        self.assertAlmostEqual(ch.percentile([0, 10], 0.25), 2.5)

    def test_a_single_value_is_its_own_percentile(self):
        self.assertEqual(ch.percentile([7], 0.9), 7)

    def test_empty_raises_rather_than_returning_zero(self):
        with self.assertRaises(ValueError):
            ch.percentile([], 0.5)


class TestTheSuggestionCannotBindOnKnownHistory(unittest.TestCase):
    """
    ⚠️ 這是整個檔案的重點。

    門檻設在歷史最大值之下,就是在說回測裡某幾天不該那樣做 ——
    而回測說它們沒問題。那個門檻會讓 Calmar 1.33 失效,
    而它會**安靜地**失效:沒有人會重跑回測去發現。
    """

    def suggestion_for(self, ceiling):
        return ch.suggest({"equivalent_pct": {"max": ceiling}})

    def test_the_suggestion_is_always_above_the_historical_max(self):
        for ceiling in (12.3, 44.0, 45.0, 49.9, 63.7, 88.2, 100.0):
            with self.subTest(max=ceiling):
                pick = self.suggestion_for(ceiling)
                self.assertGreater(
                    pick["max_equivalent_exposure_pct"], ceiling,
                    f"建議 {pick['max_equivalent_exposure_pct']} 低於或等於"
                    f"歷史最大 {ceiling} —— 這條門檻在歷史上會觸發,"
                    "Calmar 1.33 就不算數了")

    def test_an_exact_multiple_of_five_still_gets_headroom(self):
        """
        邊界:歷史最大剛好是 45.0 時,取整不會往上走,
        全靠那 10 個百分點的緩衝。
        """
        pick = self.suggestion_for(45.0)
        self.assertEqual(pick["max_equivalent_exposure_pct"], 55.0)

    def test_it_explains_itself(self):
        pick = self.suggestion_for(44.0)
        self.assertIn("歷史最大", pick["why"])
        self.assertIn("不需要重新回測", pick["why"])


class TestItOnlyReads(unittest.TestCase):
    """
    一個會動到帳本的「分析腳本」是災難。
    """

    WRITE_CALLS = {"write_json_atomic", "_append", "save", "unlink",
                   "write_text", "write_bytes", "mkdir", "rename"}

    def test_no_write_calls_anywhere_in_the_script(self):
        path = ROOT / "scripts" / "concentration_history.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = None
            if isinstance(node.func, ast.Attribute):
                name = node.func.attr
            elif isinstance(node.func, ast.Name):
                name = node.func.id
            if name in self.WRITE_CALLS:
                found.append(f"第 {node.lineno} 行 {name}()")

        self.assertEqual(found, [], "分析腳本裡有寫入呼叫:" + "; ".join(found))

    def test_it_does_not_open_anything_for_writing(self):
        path = ROOT / "scripts" / "concentration_history.py"
        body = path.read_text(encoding="utf-8")
        for mode in ('"w"', "'w'", '"a"', "'a'", '"wb"', '"ab"'):
            self.assertNotIn(f"open({mode}", body)
            self.assertNotIn(f", {mode})", body)


class TestItCannotSeeTheFuture(unittest.TestCase):

    def test_the_walk_uses_a_point_in_time_correlation(self):
        """
        用全期相關性去回答「那一天該不該擋」是看未來 ——
        而且方向是把門檻訂得太鬆(第三十四條)。
        """
        import inspect

        source = inspect.getsource(ch.walk)
        self.assertIn("daily_returns(idx, syms, dates, i, lookback)", source,
                      "相關性必須只用到第 i 日為止的報酬")


if __name__ == "__main__":
    unittest.main()
