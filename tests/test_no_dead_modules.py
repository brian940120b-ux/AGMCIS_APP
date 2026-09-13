"""
沒有人 import 的模組,不該留在 repo 裡。

═══ 為什麼這條值得寫成測試 ═══
`models/signal.py` 在 repo 裡躺了很久。160 行,定義了 `Signal`、
`MarketRegime`、`RiskStatus`、`RiskDecision` —— 看起來像這套系統的
訊號契約。**沒有任何一個檔案 import 它。**

它其實是**舊系統**的化石。檔頭寫著「所有 Agent / 策略 / LLM 的輸出
都必須通過此 schema 驗證」—— 而 2026-09-08 重建之後,這套系統
刻意不做 Agent(舊系統死於 1391 個配置的搜尋空間)。那份契約沒有
任何東西要遵守。

危險的地方不是它佔 160 行,是它**看起來是活的**:

  · 有人要找「MarketRegime 怎麼算的」,會找到這個型別,
    然後以為系統有在算行情狀態。實際上一行都沒有。
  · `RiskDecision` 這個名字在 `portfolio/risk.py` 裡也有一個,
    是另一個完全不同的類別。兩個同名的東西,一個是活的一個是死的。

`CLAUDE.md` 教訓第 4 條:「面板讀得到 trailing_stop_pct,而引擎裡
出現 0 次 —— 看起來存在、實際無作用的參數,比沒有更糟。」
死模組是同一件事,只是尺度大了一號。

═══ 這條測試不擋什麼 ═══
不擋「暫時還沒接上」的新模組 —— 那種東西應該在分支上,不在主線。
真的需要保留一個沒人 import 的模組,就把它加進 ALLOWED 並寫明理由。
**加進 ALLOWED 是一個要打字的動作**,那正是重點:它會出現在 diff 裡。
"""
import ast
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 掃哪些套件。scripts/ 不掃 —— 那些是進入點,本來就沒有人 import 它們。
PACKAGES = ("core", "exchange", "market_data", "models", "notify", "portfolio")

# 明確允許沒有人 import 的檔案,每一條都要寫理由。
ALLOWED = {
    "portfolio/run.py":
        "排程進入點,由 systemd 呼叫",

    "portfolio/universe.py":
        "手動跑的篩選工具(有 __main__),不是被 import 的模組",

    # ⚠️ 這一條不是「沒關係」,是「知道了,而且知道代價」。
    #
    # liquidity.py 算的是**每個幣自己的滑點**,理由寫在它的檔頭:
    # 「用平均值套所有幣,等於同時高估了大幣的成本、低估了小幣的
    # 成本。兩邊都錯。」
    #
    # 而 costs.py 到今天仍然是 SLIP_FLOOR_PCT = 0.02,對七個幣一視同仁。
    # 也就是說 —— **它要解決的問題,一天都沒有被解決過。**
    #
    # 那為什麼不現在接上去?因為接上去會改變回測的成本,
    # Calmar 1.33 就要重新驗證。那屬於「動到策略」的那一批,
    # 不能夾在一次補洞裡偷偷做掉(第三十四條)。
    #
    # 留著它、並在這裡寫明它沒有接上,比刪掉它好:刪掉會讓下一個人
    # 以為這件事從來沒有人想過。
    "market_data/liquidity.py":
        "逐幣滑點模型,尚未接進成本模型 —— 接上去會改變回測結果,"
        "屬於要重新回測的那一批",
}


def modules_under(package: str) -> dict:
    """{'portfolio/risk.py': 'portfolio.risk'}"""
    out = {}
    for path in sorted((ROOT / package).rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if path.name == "__init__.py":
            continue
        out[rel] = rel[:-3].replace("/", ".")
    return out


class Unparseable(Exception):
    """有檔案讀不進 AST。這件事本身就是失敗,不能默默跳過。"""


def importers_of_everything() -> dict:
    """
    {被 import 的模組名: {是誰 import 它的}}

    收「完整路徑」與「模組.名字」兩種寫法:`import portfolio.risk` 與
    `from portfolio import risk` 是同一件事,但 AST 節點不一樣。

    記「是誰」而不只是「有沒有」,是為了排掉自我引用 —— 一個模組
    import 自己不能算它是活的。

    ═══ 讀不進去的檔案一律拋例外 ═══
    這裡原本寫 `except SyntaxError: continue`。結果是:`scripts/daily.py`
    在 Python 3.11 下解析失敗(它有一個 3.12 才合法的 f-string),
    於是它 import 的四個模組 —— core/config、notify/telegram、
    market_data/liquidity、portfolio/universe —— **全部被誤判成死碼**。

    差一點就把四個活著的模組刪掉了。而那個 `continue` 一聲都不會出。

    第九十四條:不准靜默失敗。掃描工具自己更不行 ——
    一個會漏看檔案的掃描器,給出的是**有信心的錯答案**。
    """
    found: dict[str, set] = {}

    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d != "__pycache__"]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = Path(dirpath) / filename
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"),
                                 filename=str(path))
            except (SyntaxError, UnicodeDecodeError) as e:
                raise Unparseable(
                    f"{path.relative_to(ROOT)} 讀不進 AST:{e}\n"
                    f"(這個直譯器是 {sys.version.split()[0]})\n"
                    "在跳過它之前先想一下:這支腳本在 VPS 上跑得起來嗎?"
                ) from e

            here = path.relative_to(ROOT).as_posix()[:-3].replace("/", ".")

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        found.setdefault(alias.name, set()).add(here)
                elif isinstance(node, ast.ImportFrom):
                    if node.level or not node.module:
                        continue
                    found.setdefault(node.module, set()).add(here)
                    # `from portfolio import risk` —— risk 是模組不是名字
                    for alias in node.names:
                        found.setdefault(
                            f"{node.module}.{alias.name}", set()).add(here)

    return found


class TestNothingIsDeadCode(unittest.TestCase):

    def test_every_module_is_imported_by_something(self):
        importers = importers_of_everything()

        dead = []
        for package in PACKAGES:
            if not (ROOT / package).is_dir():
                continue
            for rel, dotted in modules_under(package).items():
                if rel in ALLOWED:
                    continue
                # 自己 import 自己不算活著
                if importers.get(dotted, set()) - {dotted}:
                    continue
                dead.append(rel)

        self.assertEqual(
            dead, [],
            "這些模組沒有任何檔案 import:\n  "
            + "\n  ".join(dead)
            + "\n\n看起來存在、實際無作用的東西,比沒有更糟"
            "(CLAUDE.md 教訓第 4 條)。\n"
            "刪掉它,或加進 ALLOWED 並寫明為什麼要留。",
        )

    def test_the_allowed_list_has_no_stale_entries(self):
        """
        ALLOWED 裡指向不存在的檔案,代表那個豁免已經沒有意義了 ——
        留著只會讓下一個人以為那個檔案還在。
        """
        for rel in ALLOWED:
            with self.subTest(path=rel):
                self.assertTrue(
                    (ROOT / rel).exists(),
                    f"ALLOWED 裡的 {rel} 不存在了,把它從清單移掉",
                )


if __name__ == "__main__":
    unittest.main()


class TestTheScanItselfIsHonest(unittest.TestCase):
    """
    掃描器漏看檔案時必須出聲,不能默默給一個少了東西的答案。
    """

    def test_a_file_it_cannot_parse_raises_instead_of_being_skipped(self):
        scratch = ROOT / "portfolio" / "_tmp_unparseable.py"
        scratch.write_text("def broken(:\n", encoding="utf-8")
        try:
            with self.assertRaises(Unparseable):
                importers_of_everything()
        finally:
            scratch.unlink(missing_ok=True)

    def test_every_file_in_the_repo_parses_on_this_interpreter(self):
        """
        直接講清楚這個 repo 需要哪個 Python。

        `scripts/daily.py` 曾經只有 3.12 以上讀得懂 —— 而它是每日記帳
        與日報的進入點。在 3.11 的機器上,它不是「功能怪怪的」,
        是**載入時就 SyntaxError,一行都跑不到**。
        Ubuntu 22.04 的預設 Python 是 3.10。
        """
        try:
            importers_of_everything()
        except Unparseable as e:
            self.fail(str(e))
