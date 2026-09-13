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
    # 出場調校(第五十六節)。它寫一份 JSON 報告,但**不改任何參數** ——
    # 第七十八節:自己修改 → 自己測試 → 自己批准,這條鏈不成立。
    "tune_exits.py",
    # 實單路徑檢視(第七十八 / 一百零三節)。它印出實單原始碼的雜湊,
    # 然後跑既有的行為測試 —— 不重寫一套檢查,也不碰交易所。
    "verify_live_broker.py",
    # SIMULATED LIVE(第十八 / 一百零六節)。它跑的是模擬交易所,
    # 沒有網路、沒有金鑰、沒有真錢 —— 但它會呼叫 create_order,
    # 所以下面的禁止清單掃描要對它放行那幾個名字,見 SIMULATED。
]

# 這一支會呼叫下單相關的名字,但對象是**模擬交易所**,不是 BingX。
# 整支排除在檢查外是不對的(那等於開一個沒有邊界的洞),所以只放行
# 它必須用到的那幾個,其餘照掃。
SIMULATED = {
    "simulate_live.py": {"create_order", "cancel_order", "set_leverage",
                         "close_position"},
}

# 這些腳本會寫檔,而且那就是它們的職責。
#
# 它們仍然要通過禁止清單的掃描 —— 「會寫檔」不等於「可以下單」。
# 把它們整支排除在檢查外,等於在唯讀規則上開一個沒有邊界的洞。
WRITES_BY_DESIGN = {
    "migrate.py": "跑資料庫 migration",
    "live_confirm.py": "寫 LIVE 確認檔(第九十二節)",
    "calendar.py": "維護事件日曆(第五十一節)",
}

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

    def test_scripts_that_write_by_design_still_cannot_trade(self):
        """
        「會寫檔」不等於「可以下單」。這些腳本一樣不准碰交易路徑。
        """
        for name in WRITES_BY_DESIGN:
            path = os.path.join(SCRIPTS_DIR, name)
            self.assertTrue(os.path.exists(path), f"{name} 不存在")

            for called in calls_in(path):
                with self.subTest(script=name, call=called):
                    self.assertNotIn(called, FORBIDDEN_CALLS)

    def test_the_simulated_script_only_touches_the_simulator(self):
        """
        放行清單必須有邊界。這一支可以呼叫下單相關的名字,但:

          1. 只有明列的那幾個,其餘照擋。
          2. 它不可以 import 任何真的能連到交易所的東西。

        少了第 2 點,「它跑的是模擬器」就只是一句話而不是一個事實。
        """
        for name, allowed in SIMULATED.items():
            path = os.path.join(SCRIPTS_DIR, name)
            self.assertTrue(os.path.exists(path), f"{name} 不存在")

            for called in calls_in(path):
                if called in allowed:
                    continue
                with self.subTest(script=name, call=called):
                    self.assertNotIn(called, FORBIDDEN_CALLS)

            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)

            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(a.name for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)

            for forbidden in ("ccxt", "agmcis.exchange.bingx",
                              "agmcis.data.market_data", "market_data"):
                with self.subTest(script=name, imports=forbidden):
                    self.assertFalse(
                        any(m == forbidden or m.startswith(forbidden + ".")
                            for m in imported),
                        f"{name} import 了 {forbidden} —— 那條路連得到交易所",
                    )

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
        known = (set(READ_ONLY_SCRIPTS) | set(WRITES_BY_DESIGN)
                 | set(SIMULATED) | {"__init__.py"})
        present = {
            name for name in os.listdir(SCRIPTS_DIR)
            if name.endswith(".py")
        }

        unlisted = present - known
        self.assertEqual(
            unlisted, set(),
            f"這些腳本沒有被納入唯讀檢查:{sorted(unlisted)}。"
            f"如果它本來就會寫檔(例如 migrate.py),把它加進 WRITES_BY_DESIGN;"
            f"否則加進 READ_ONLY_SCRIPTS。兩邊都會掃禁止清單。",
        )


if __name__ == "__main__":
    unittest.main()
