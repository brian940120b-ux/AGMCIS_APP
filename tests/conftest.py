"""
測試前置 —— 分辨「程式壞了」與「環境沒裝好」。

═══ 這支存在的理由 ═══
在一台乾淨的機器上 clone 下來直接 `pytest`,會看到 **23 條紅字**。
它們全部來自同一個原因:`data/bingx_specs.json` 不存在。

那個檔案是交易所合約規格的落地快取,由 `daily.py` 每日更新;
`data/` 在 .gitignore 裡,所以它不會跟著 repo 走 —— 這是對的,
快取不該進版控。但結果是:**測試套件只在跑過一次 refresh 的機器上是綠的。**

23 條 AssertionError 看起來像 23 個 bug。實際上是一件事:
你還沒有跟交易所要過規格。這正是教訓第 5 條說的那種情況 ——
訊號存在,但它指向錯的方向,於是久了就被當成背景雜訊忽略。

═══ 為什麼不放一份假的規格進去 ═══
想過。但 `test_fee_constant_still_matches_the_exchange` 這種測試的
**全部意義**就是拿程式裡的常數去對交易所真實的費率。餵它一份我自己
編的數字,它就從「交易所改費率會變紅」退化成「我抄的數字等於我抄的
數字」—— 一條永遠是綠的測試,比沒有那條測試更糟(教訓第 4 條)。

第十二節寫得很清楚:規格一律從交易所動態取得,不可寫死。
那條規則在測試裡同樣成立。

═══ 怎麼知道某條測試是「因為沒快取」才紅的 ═══
不能看 traceback。`PaperExecutor.submit` 會把 SpecMissing 吞掉、
記一筆 log、然後回 status=REJECTED —— 測試看到的是一個斷言不符,
例外鏈裡什麼都沒有。

所以判準有兩條,兩條都是「看得到證據」而不是猜:

  1. `specs._load` 這一輪有沒有拋過 SpecMissing。
  2. 這一輪有沒有出現 FileNotFoundError,而且指向的正是 `data/` 底下
     的某個快取檔(規格之外還有資金費率 `bingx_funding.json`)。

`data/` 以外的 FileNotFoundError 不算 —— 那是真的找不到檔案。

═══ skip 不是綠燈 ═══
pytest 總結會列出「N skipped」,理由就寫在那裡,而且附上要跑哪一行。
**它絕不會把「沒驗證」講成「驗證過了」。**
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CACHE = ROOT / "data" / "bingx_specs.json"

WHY = (
    "data/ 底下的交易所快取不存在(合約規格 / 資金費率)。\n"
    "這不是程式壞了,是這台機器還沒跟 BingX 要過資料。\n"
    "跑這兩行之後再測一次:\n"
    "    python -c 'from portfolio.specs import refresh; refresh()'\n"
    "    python -c 'from portfolio.specs import refresh_funding;"
    " from portfolio.paper import SYMBOLS; refresh_funding(SYMBOLS)'\n"
    "(都是公開端點,不需要 API 金鑰)"
)

DATA = ROOT / "data"

# 這一輪測試裡,有沒有踩到「快取不在」。
_missed = {"hit": False}


def _is_a_missing_cache(exc) -> bool:
    """
    這顆例外是不是「`data/` 底下的快取檔不存在」?

    只認 `data/` 底下的。程式在別的地方找不到檔案是真的 bug,
    不該被這支洗成 skip。
    """
    seen = []
    while exc is not None and exc not in seen:
        seen.append(exc)
        if isinstance(exc, FileNotFoundError) and exc.filename:
            try:
                Path(exc.filename).resolve().relative_to(DATA.resolve())
                return True
            except ValueError:
                pass
        exc = exc.__cause__ or exc.__context__
    return False


@pytest.fixture(autouse=True)
def _watch_for_missing_specs(monkeypatch):
    """
    包住 `specs._load`,只做一件事:記下它有沒有拋 SpecMissing。

    不改行為、不吞例外、不給假資料 —— 拋出去的還是原本那顆。
    """
    _missed["hit"] = False

    if CACHE.exists():
        yield
        return

    from portfolio import specs

    original = specs._load

    def watched():
        try:
            return original()
        except specs.SpecMissing:
            _missed["hit"] = True
            raise

    monkeypatch.setattr(specs, "_load", watched)
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """
    只有「這條測試紅了 **而且** specs 這一輪確實讀不到」才改判 skip。

    其他任何失敗照樣是失敗 —— 這支不是用來讓紅字變少的,
    是用來讓剩下的紅字真的代表 bug。
    """
    outcome = yield
    report = outcome.get_result()

    if CACHE.exists() or report.when != "call" or not report.failed:
        return
    if not _missed["hit"] and not (
            call.excinfo is not None
            and _is_a_missing_cache(call.excinfo.value)):
        return

    report.outcome = "skipped"
    report.longrepr = (str(item.fspath), item.location[1] + 1, WHY)
