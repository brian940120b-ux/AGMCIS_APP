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


if __name__ == "__main__":
    unittest.main()
