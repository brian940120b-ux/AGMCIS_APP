"""
所有「檢查 / 報告」類腳本都必須是唯讀的。

這些腳本會在**有金鑰的機器**上執行,而且常常是在出事的時候執行 ——
那正是最不該有副作用的時刻。一支在你診斷問題時順手下了一張單的腳本,
會讓事情變得更難收拾。

規則用 AST 掃描,不是靠人記得。
"""
import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts",
)

# 這些腳本的職責就是唯讀檢查
READ_ONLY_SCRIPTS = [
    "verify_bingx.py",
    "calibration_report.py",
    "preflight.py",
    "live_gate.py",
    "run_strategy_lab.py",
]

FORBIDDEN_CALLS = {
    # 交易所寫入
    "create_order", "cancel_order", "set_leverage", "set_margin_mode",
    "withdraw", "transfer", "close_position",
    # 本地寫入
    "create_paper_trade", "close_paper_trade", "insert_trade",
    "close_trade_atomic", "update_account",
    # 狀態變更
    "arm", "panic", "run_auto_trader",
}


def calls_in(path):
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        yield (
            func.attr if isinstance(func, ast.Attribute)
            else func.id if isinstance(func, ast.Name)
            else None
        )


class TestScriptsAreReadOnly(unittest.TestCase):

    def test_no_read_only_script_writes_anything(self):
        for name in READ_ONLY_SCRIPTS:
            path = os.path.join(SCRIPTS_DIR, name)
            self.assertTrue(os.path.exists(path), f"{name} 不存在")

            for called in calls_in(path):
                with self.subTest(script=name, call=called):
                    self.assertNotIn(called, FORBIDDEN_CALLS)

    def test_every_script_in_the_list_still_exists(self):
        """
        腳本被改名或刪掉時,上面的迴圈會靜靜地少檢查一支。
        """
        for name in READ_ONLY_SCRIPTS:
            with self.subTest(script=name):
                self.assertTrue(os.path.exists(os.path.join(SCRIPTS_DIR, name)))

    def test_new_scripts_are_not_silently_unchecked(self):
        """
        新增一支檢查腳本卻忘了加進清單,等於它沒有被檢查過。
        這裡列出漏掉的,強迫做個決定。
        """
        known = set(READ_ONLY_SCRIPTS) | {"migrate.py", "__init__.py"}
        present = {
            name for name in os.listdir(SCRIPTS_DIR)
            if name.endswith(".py")
        }

        unlisted = present - known
        self.assertEqual(
            unlisted, set(),
            f"這些腳本沒有被納入唯讀檢查:{sorted(unlisted)}。"
            f"如果它本來就會寫入(例如 migrate.py),把它加進 known;"
            f"否則加進 READ_ONLY_SCRIPTS。",
        )


if __name__ == "__main__":
    unittest.main()
