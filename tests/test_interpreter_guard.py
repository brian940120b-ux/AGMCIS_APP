"""
用錯 Python 的時候要講人話。

═══ 2026-09-13 ═══
在 VPS 上跑 `python3 scripts/concentration_history.py`,拿到的是:

    File ".../models/market.py", line 14
    ModuleNotFoundError: No module named 'pydantic'

那則訊息是真的,但它指向錯的地方。pydantic 有在 requirements.txt 裡,
venv 裡也裝好了 —— 問題是這支腳本被系統 python 跑,而服務跑的是
`.venv/bin/python`。

看到那則訊息的人會去 `pip install pydantic` 裝到系統 python 上,
於是有兩份互不相干的環境,而下一個錯誤會更難查。

而且它一定會再發生:models/market.py 需要 pydantic,而它是
market_data/history.py 的必經之路 —— 任何要讀 K 線的腳本都繞不過。
"""
import ast
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import interpreter


class TestItDetectsAMissingPackage(unittest.TestCase):

    def test_a_healthy_environment_reports_nothing_missing(self):
        self.assertEqual(interpreter.missing(), [])

    def test_require_is_silent_when_everything_is_there(self):
        interpreter.require()          # 不該拋

    def test_it_exits_rather_than_raising_a_module_error(self):
        with patch.object(interpreter, "missing", lambda: ["pydantic"]):
            with self.assertRaises(SystemExit) as caught:
                interpreter.require()
        self.assertEqual(caught.exception.code, 2)


class TestTheMessageActuallyHelps(unittest.TestCase):
    """
    一則說「少了 pydantic」的訊息,會讓人去裝 pydantic ——
    而那是錯的解法。訊息要指向真正的原因。
    """

    def test_it_names_the_venv_interpreter_when_one_exists(self):
        with patch.object(interpreter, "VENV_PYTHON",
                          Path(__file__)):        # 一定存在的檔案
            text = interpreter.explain(["pydantic"])

        self.assertIn("直譯器用錯了", text)
        self.assertIn(sys.executable, text, "要說出「你現在用的是哪一個」")
        self.assertIn("不要 pip install 到系統 python", text)

    def test_it_tells_you_how_to_build_the_venv_when_there_is_none(self):
        with patch.object(interpreter, "VENV_PYTHON",
                          Path("/nonexistent/.venv/bin/python")):
            text = interpreter.explain(["pydantic"])

        self.assertIn("python3 -m venv", text)
        self.assertIn("requirements.txt", text)

    def test_it_never_switches_interpreters_by_itself(self):
        """
        一個會偷偷換掉自己執行環境的腳本,除錯的時候會讓人瘋掉。
        """
        body = (ROOT / "core" / "interpreter.py").read_text(encoding="utf-8")
        tree = ast.parse(body)

        banned = {"execv", "execve", "execvp", "check_call", "run", "Popen"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func,
                                                         ast.Attribute):
                self.assertNotIn(
                    node.func.attr, banned,
                    f"第 {node.lineno} 行想自己啟動別的行程 —— 不可以")


class TestEveryScriptChecksBeforeItBreaks(unittest.TestCase):
    """
    守門要在**會炸的 import 之前**,否則它一點用都沒有。
    """

    HEAVY = ("portfolio", "market_data", "models")

    def scripts(self):
        return sorted((ROOT / "scripts").glob("*.py"))

    def needs_a_guard(self, tree) -> bool:
        """
        只有會碰到本專案重量級模組的腳本需要守門。

        純 stdlib 的腳本(例如 archivist.py 只用 tarfile / subprocess)
        用系統 python 跑得起來,硬加一條檢查只是雜訊 ——
        而**沒有必要的檢查久了就會被習慣性略過**,連帶讓有必要的
        那幾條一起失效。
        """
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in self.HEAVY:
                    return True
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in self.HEAVY:
                        return True
        return False

    def test_every_script_that_needs_a_guard_has_one(self):
        missing = []
        for path in self.scripts():
            if path.name == "__init__.py":
                continue
            body = path.read_text(encoding="utf-8")
            if not self.needs_a_guard(ast.parse(body, filename=str(path))):
                continue
            if "interpreter.require()" not in body:
                missing.append(path.name)

        self.assertEqual(
            missing, [],
            "這些腳本會 import 需要 pydantic 的模組,卻沒有守門 —— "
            "用錯 Python 時會丟一個看不懂的 ModuleNotFoundError:"
            + "、".join(missing))

    def test_the_guard_runs_before_any_project_import(self):
        """
        排在 `from portfolio... import` 後面的守門,永遠不會有機會執行
        —— 那行 import 會先炸。
        """
        for path in self.scripts():
            if path.name == "__init__.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"),
                             filename=str(path))
            if not self.needs_a_guard(tree):
                continue

            guard_line = None
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "require"
                        and getattr(node.func.value, "id", "")
                        == "interpreter"):
                    guard_line = node.lineno
                    break

            heavy = [n.lineno for n in tree.body
                     if isinstance(n, ast.ImportFrom) and n.module
                     and n.module.split(".")[0] in self.HEAVY]

            with self.subTest(script=path.name):
                self.assertIsNotNone(guard_line, "沒有守門")
                if heavy:
                    self.assertLess(
                        guard_line, min(heavy),
                        f"守門在第 {guard_line} 行,但第 {min(heavy)} 行就"
                        "已經 import 了會炸的模組 —— 守門永遠不會執行到")


if __name__ == "__main__":
    unittest.main()
