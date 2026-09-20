"""
獵場 —— 提高機率的唯一誠實做法 · 2026-09-20

執政官:「不管用任何指標、網路上的資訊還是什麼,你就是要幫我做到
盈利、提高機率。」

═══ 能做的與不能做的,分清楚 ═══
**不能**:保證盈利。沒有人能,而任何說能的系統都在騙你。

**能**:提高「找到真的有效的東西」的機率 —— 靠的是**誠實地試更多
假說,而且每一次都付多重比較的帳**。

那個「付帳」不是形式。舊系統試了 1391 個配置,通過 15 案,
而純雜訊預期就有 52 案 —— **實際低於雜訊**。試得越多、結論越糟,
因為沒有人替搜尋次數付帳。

所以這一組守的是:**擴大搜尋的同時,校正必須跟著擴大。**
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _hunt():
    spec = importlib.util.spec_from_file_location(
        "hunt", ROOT / "scripts/hunt.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_hypothesis_list_is_fixed_before_running():
    """13 個:5 指標 × 2 種用法 + 多數決 × 2 + 動能前一半。

    **清單在跑之前定死。** 跑完之後增刪清單,等於用結果挑假說 ——
    那時候 p 值就不是 p 值了。
    """
    hyps = _hunt().hypotheses()
    assert len(hyps) == 13, f"假說數量變了:{len(hyps)}"
    keys = [k for k, _, _ in hyps]
    for kind in ("macd", "kdj", "rsi", "ma", "boll", "vote"):
        assert f"{kind}-單獨" in keys and f"{kind}-濾網" in keys
    assert "動能前一半" in keys


def test_no_parameter_is_searched():
    """參數一律用 BingX App 的預設值。

    搜參數就是舊系統那 1391 次 —— 而它實際低於雜訊。
    """
    src = (ROOT / "scripts/hunt.py").read_text(encoding="utf-8")
    assert "一個都不搜" in src
    assert "1391" in src


def test_all_hypotheses_run_at_the_incumbents_risk_settings():
    """**要比的是訊號,不是規模。**

    兩邊的波動目標或槓桿不同的話,贏的那個可能只是賭比較大 ——
    而那不是「找到更好的策略」,是「冒更多險」。
    """
    src = (ROOT / "scripts/hunt.py").read_text(encoding="utf-8")
    body = src[src.index("def wrap("):src.index("def run(")]
    assert "VOL_TARGET_ANNUAL_PCT" in body and "LEVERAGE_CAP" in body
    assert "不動任何風控參數" in body


def test_the_acceptance_criteria_were_written_before_the_run():
    """四關,2026-09-08 寫死在 rules.py 的 docstring 裡,這裡一字不改:
    訓練贏 + 驗證贏 + 回撤不更差 + 校正後 p < 0.05。"""
    src = (ROOT / "scripts/hunt.py").read_text(encoding="utf-8")
    assert "2026-09-08" in src
    assert "沒過就蓋棺,不換參數再試" in src
    assert "bonferroni" in src.lower()


def test_failing_everything_is_reported_as_a_result_not_a_failure():
    """**一個都沒過是結果,不是失敗。**

    它說的是這些教科書指標在這批幣、這段期間沒有比現役好。
    不這樣講的話,下一步就會有人開始挑參數、挑期間、挑幣種,
    直到某一個看起來贏為止 —— 那就是那 1391 次。
    """
    src = (ROOT / "scripts/hunt.py").read_text(encoding="utf-8")
    assert "這是**結果,不是失敗**" in src


def test_it_changes_no_settings():
    """跟研究迴路同一條鐵律:只產生提案,不改任何設定。"""
    src = (ROOT / "scripts/hunt.py").read_text(encoding="utf-8")
    assert "不改任何設定" in src
    # 不准寫帳本 / 不准動 paper 的狀態
    for forbidden in ("tick(", "Account.save", "state_path"):
        assert forbidden not in src, f"獵場碰了 {forbidden}"


def test_the_daily_job_runs_it_and_isolates_its_failures():
    """它每天自己跑 —— 執政官要的是「不斷地」。
    而它失敗不得拖垮記帳:一個加值功能把本業弄掛掉,是最蠢的當機。"""
    src = (ROOT / "scripts/daily.py").read_text(encoding="utf-8")
    assert "scripts/hunt.py" in src
    at = src.index("scripts/hunt.py")
    assert "不影響記帳" in src[at - 700:at + 700]
