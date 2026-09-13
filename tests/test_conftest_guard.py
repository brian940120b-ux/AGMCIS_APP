"""
看門的自己要有人看。

conftest.py 會把某些紅字改判成 skip。那是一個**降低紅字數量**的機制,
而降低紅字數量的機制,如果自己壞掉,就是最危險的那種壞法 ——
教訓第 5 條:「說謊的稽核比沒有稽核危險」。

所以這一組只驗一件事:**改判的邊界在該在的地方。**

  · 一條普通的失敗,必須還是失敗。
  · 一條因為 data/ 快取不在而失敗的,才可以是 skip。
  · data/ 以外的 FileNotFoundError,必須還是失敗。

用子行程真的跑一次 pytest,而不是去呼叫 hook —— 直接呼叫 hook
驗的是「我寫的函式做了我以為的事」,跑一次驗的是「pytest 真的照做」。
"""
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data"


def run_one(tmp_path, body: str) -> str:
    """在 repo 裡跑一支臨時測試,回傳 pytest 的輸出。

    必須放在 tests/ 底下,否則 conftest.py 不會生效 —— 而 conftest
    生不生效正是這裡要驗的東西。
    """
    scratch = ROOT / "tests" / "_tmp_guard_test.py"
    scratch.write_text(textwrap.dedent(body), encoding="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(scratch), "-q", "--no-header",
             "-p", "no:cacheprovider"],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
        )
        return proc.stdout + proc.stderr
    finally:
        scratch.unlink(missing_ok=True)


def test_an_ordinary_failure_stays_a_failure(tmp_path):
    """
    最重要的一條。如果這條變綠,整個測試套件就失去意義了。
    """
    out = run_one(tmp_path, '''
        def test_this_must_stay_red():
            assert 1 == 2
    ''')

    assert "1 failed" in out, (
        "一條普通的斷言失敗被 conftest 洗掉了。"
        f"\n\npytest 輸出:\n{out}"
    )


def test_a_missing_file_outside_data_stays_a_failure(tmp_path):
    """
    只有 `data/` 底下的快取才算「環境沒裝好」。
    程式在別的地方找不到檔案是真的 bug。
    """
    out = run_one(tmp_path, '''
        from pathlib import Path

        def test_this_must_stay_red():
            Path("/nonexistent/definitely/not/data/thing.json").read_text()
    ''')

    assert "1 failed" in out, (
        "data/ 以外的 FileNotFoundError 被當成快取缺失了。"
        f"\n\npytest 輸出:\n{out}"
    )


@pytest.mark.skipif(
    (CACHE_DIR / "bingx_specs.json").exists(),
    reason="這台機器有快取,改判機制本來就不會啟動(VPS 上會走這裡)",
)
def test_a_missing_data_cache_becomes_a_skip(tmp_path):
    """
    另一半:該被改判的,真的有被改判。

    這條在有快取的機器上會 skip —— 那是對的,那台機器上根本不該
    有東西被改判。
    """
    out = run_one(tmp_path, '''
        from portfolio import specs

        def test_reads_a_spec():
            specs.spec("BTC-USDT")
    ''')

    assert "1 skipped" in out, (
        "快取缺失沒有被改判成 skip,使用者會看到一條看起來像 bug 的紅字。"
        f"\n\npytest 輸出:\n{out}"
    )
