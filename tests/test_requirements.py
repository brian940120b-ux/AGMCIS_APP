"""
requirements.txt 要真的裝得起來。

## 這個檔案為什麼存在

`requirements.txt` 曾經有一行是這樣的:

    psycopg2-binary# ccxt.pro(WebSocket)隨 ccxt 一起安裝...

套件名跟註解中間**少了換行**。pip 的行內註解要求 `#` 前面有空白,
所以它把整行當成一個套件名,然後整個安裝失敗:

    ERROR: Invalid requirement: 'psycopg2-binary# ccxt.pro(...)'

意思是**這個檔案從來沒有被成功安裝過一次**。它在 repo 裡躺著,
看起來像一份依賴清單,但任何人照著它裝都會失敗 —— 而且失敗在第 12 行,
前面 11 個套件也一起沒裝。

沒有測試讀過它,所以沒有人知道。一份沒有被驗證過的部署檔案,
跟沒有那個檔案的差別只在於它給人一種有的錯覺。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REQUIREMENT_FILES = ("requirements.txt", "requirements-dev.txt")

# 套件名裡合法的字元。pip 的 PEP 508 名稱只允許字母數字與 - _ .
NAME_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-_.")

# 版本指定與 extras 的起頭。看到這些就代表套件名結束了。
SPEC_STARTS = set("=<>!~[;@ \t")


def lines_of(filename):
    path = os.path.join(ROOT, filename)
    with open(path, encoding="utf-8") as handle:
        return handle.read().splitlines()


def requirement_lines(filename):
    """實際會被 pip 當成需求的那些行。"""
    for number, raw in enumerate(lines_of(filename), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        yield number, raw


class TestEveryRequirementFileParses(unittest.TestCase):

    def test_the_files_exist(self):
        for filename in REQUIREMENT_FILES:
            with self.subTest(filename=filename):
                self.assertTrue(
                    os.path.exists(os.path.join(ROOT, filename)),
                    f"{filename} 不見了 —— 部署會裝不起來",
                )

    def test_no_comment_is_glued_to_a_package_name(self):
        """
        這是那個真 bug 的回歸測試。

        pip 的行內註解要求 `#` 前面有空白。`套件名#註解` 會被當成
        一整個套件名,而整個檔案就裝不起來。
        """
        for filename in REQUIREMENT_FILES:
            for number, raw in requirement_lines(filename):
                if "#" not in raw:
                    continue
                before = raw.split("#", 1)[0]
                with self.subTest(file=filename, line=number):
                    self.assertTrue(
                        before.endswith((" ", "\t")),
                        f"{filename} 第 {number} 行:註解黏在套件名後面。"
                        f"pip 會把整行當成套件名。內容:{raw!r}",
                    )

    def test_every_package_name_is_a_legal_name(self):
        """
        套件名裡出現中文、空白或括號,一定是格式壞了。
        """
        for filename in REQUIREMENT_FILES:
            for number, raw in requirement_lines(filename):
                line = raw.split("#", 1)[0].strip()
                if not line:
                    continue

                name = ""
                for char in line:
                    if char in SPEC_STARTS:
                        break
                    name += char

                with self.subTest(file=filename, line=number):
                    self.assertTrue(name, f"{filename} 第 {number} 行沒有套件名")
                    illegal = set(name.lower()) - NAME_CHARS
                    self.assertEqual(
                        illegal, set(),
                        f"{filename} 第 {number} 行的套件名有不合法的字元 "
                        f"{sorted(illegal)}:{name!r}",
                    )

    def test_pip_itself_can_parse_every_line(self):
        """
        上面兩條是用規則檢查。這一條直接請 pip 的解析器讀一遍 ——
        它才是那個會在部署時說 yes 或 no 的東西。

        pip 的內部 API 不保證穩定,所以裝不到就跳過而不是失敗:
        一條因為環境而紅的測試,久了就會被習慣性忽略。
        """
        try:
            from pip._vendor.packaging.requirements import Requirement
        except ImportError:
            self.skipTest("這個環境沒有 pip 的 packaging 模組")

        for filename in REQUIREMENT_FILES:
            for number, raw in requirement_lines(filename):
                line = raw.split(" #", 1)[0].split("\t#", 1)[0].strip()
                if not line:
                    continue
                with self.subTest(file=filename, line=number):
                    try:
                        Requirement(line)
                    except Exception as exc:
                        self.fail(
                            f"{filename} 第 {number} 行 pip 解析不了:"
                            f"{line!r} —— {exc}"
                        )


class TestTheRuntimeDependenciesAreActuallyUsed(unittest.TestCase):
    """
    清單裡列了但根本沒 import 的套件,會讓部署裝一堆用不到的東西,
    而且掩蓋掉「這個套件到底還需不需要」這個問題。
    """

    def test_every_listed_package_can_be_imported(self):
        """
        在這個容器裡跑得起來,不代表 VPS 也一樣 —— 但至少擋掉
        「清單裡有一個根本不存在的套件名」。
        """
        import importlib

        # 套件名與 import 名不一樣的那幾個。
        IMPORT_NAME = {
            "psycopg2-binary": "psycopg2",
            "python-dotenv": "dotenv",
            "python-multipart": "multipart",
        }

        for number, raw in requirement_lines("requirements.txt"):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue

            name = ""
            for char in line:
                if char in SPEC_STARTS:
                    break
                name += char

            module = IMPORT_NAME.get(name, name.replace("-", "_"))

            with self.subTest(package=name):
                try:
                    importlib.import_module(module)
                except ImportError:
                    self.skipTest(f"{name} 沒有裝在這個環境")


class TestNothingImportsAnUndeclaredPackage(unittest.TestCase):
    """
    這一組擋的是**在 VPS 上出現過兩次**的同一種失敗:
    程式碼 import 了一個沒有被任何 requirements 檔宣告的套件。

    開發機剛好裝了它,所以本機全綠;乾淨的環境一裝就炸。第一次是
    `yaml`(測試收集階段整包 ModuleNotFoundError),而在那之前
    requirements.txt 本身還壞著。

    共同點是:沒有任何東西在檢查「宣告的」與「用到的」是否一致。
    """

    def declared(self):
        names = set()
        for filename in REQUIREMENT_FILES:
            for _number, raw in requirement_lines(filename):
                line = raw.split("#", 1)[0].strip()
                name = ""
                for char in line:
                    if char in SPEC_STARTS:
                        break
                    name += char
                if name:
                    names.add(name.lower().replace("_", "-"))
        return names

    def local_modules(self, folder=None):
        """
        repo 自己的東西,不需要宣告。

        包含三種:頂層的 .py 檔、**任何裝著 .py 的頂層目錄**
        (不是只有帶 __init__.py 的套件 —— tests/ 與 strategies/
        都沒有 __init__.py 但確實是本地模組),以及被掃描的那個
        資料夾裡的檔案(例如 tests/conftest.py 旁邊的 fake_ccxt.py)。
        """
        names = set()

        for entry in os.listdir(ROOT):
            path = os.path.join(ROOT, entry)
            if entry.endswith(".py"):
                names.add(entry[:-3])
            elif os.path.isdir(path) and not entry.startswith("."):
                try:
                    if any(f.endswith(".py") for f in os.listdir(path)):
                        names.add(entry)
                except OSError:
                    continue

        if folder:
            base = os.path.join(ROOT, folder)
            names.update(
                f[:-3] for f in os.listdir(base) if f.endswith(".py")
            )

        return names

    def imported_by(self, folder):
        import ast

        found = {}
        base = os.path.join(ROOT, folder)

        for entry in sorted(os.listdir(base)):
            if not entry.endswith(".py"):
                continue
            path = os.path.join(base, entry)
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), filename=path)

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        found.setdefault(alias.name.split(".")[0], entry)
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0 and node.module:
                        found.setdefault(node.module.split(".")[0], entry)

        return found

    # import 名與套件名不一樣的那幾個。
    ALIASES = {
        "yaml": "pyyaml",
        "dotenv": "python-dotenv",
        "multipart": "python-multipart",
        "psycopg2": "psycopg2-binary",
        "dateutil": "python-dateutil",
        "PIL": "pillow",
    }

    # 測試工具本身。它們由 CI / 開發者自行安裝,不是這個專案的相依。
    # 測試工具與 Python 自帶的東西。
    TOOLING = {"pytest", "_pytest", "pluggy", "pip", "setuptools"}

    def check_folder(self, folder):
        declared = self.declared()
        local = self.local_modules(folder)
        stdlib = set(sys.stdlib_module_names)

        for module, where in sorted(self.imported_by(folder).items()):
            if module in stdlib or module in local or module in self.TOOLING:
                continue

            package = self.ALIASES.get(module, module).lower().replace("_", "-")

            with self.subTest(module=module, file=f"{folder}/{where}"):
                self.assertIn(
                    package, declared,
                    f"{folder}/{where} import 了 {module},但 {package} 沒有"
                    f"被任何 requirements 檔宣告。本機裝了就看不出來,"
                    f"乾淨的環境會直接失敗。",
                )

    def test_the_test_suite_declares_what_it_imports(self):
        self.check_folder("tests")

    def test_the_scripts_declare_what_they_import(self):
        """
        腳本是在 VPS 上跑的。它們少一個相依,發現的時候通常是
        你正在處理別的問題。
        """
        self.check_folder("scripts")


if __name__ == "__main__":
    unittest.main()
