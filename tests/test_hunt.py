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
    """18 個 = 第一批 13 + 第二批 5。

    第一批(2026-09-08):6 指標 × 2 種用法 + 動能前一半 = 13
    第二批(2026-09-25):成交量 / 資金費 / 相對強弱 / 吊燈 / 低波動 = 5

    **清單在跑之前定死。** 跑完之後增刪清單,等於用結果挑假說 ——
    那時候 p 值就不是 p 值了。加一批新的可以,但必須在跑之前加完,
    而且分母跟著變大(見下面那條)。
    """
    hyps = _hunt().hypotheses({"BTC-USDT": [(0, 0.0)]})
    assert len(hyps) == 18, f"假說數量變了:{len(hyps)}"
    keys = [k for k, _, _ in hyps]
    for kind in ("macd", "kdj", "rsi", "ma", "boll", "vote"):
        assert f"{kind}-單獨" in keys and f"{kind}-濾網" in keys
    assert "動能前一半" in keys
    for k in ("B1成交量", "B2資金費", "B3相對強弱",
              "B4吊燈出場", "B5低波動"):
        assert k in keys, k


def test_a_hypothesis_with_no_data_is_dropped_not_shown_as_a_zero():
    """**沒有量到任何東西的格子,不是一次嘗試。**

    2026-09-25 第一次跑 B2 的時候,七個幣的資金費率歷史全部抓不到,
    而 B2 照樣出現在表上顯示 `0.00 / 0.00 / 0.0%`。那三個 0 看起來
    像「這個假說很爛」,實際上是它一檔都沒持有過 —— 根本沒被測到,
    卻佔了一格,還進了多重比較的分母。

    根因是 specs.refresh_funding() 會把快取整個蓋掉
    (見 tests/test_funding_cache_never_shrinks.py)。但就算根因修好,
    這一層也要擋:**資料拿不到就不要有那一格。**
    """
    hyps = _hunt().hypotheses({})                    # 沒有任何費率資料
    keys = [k for k, _, _ in hyps]
    assert "B2資金費" not in keys, "沒有資料還把 B2 留在表上"
    assert len(hyps) == 17


def test_every_hypothesis_is_built_fresh_per_window():
    """工廠,不是共用物件。

    B4(吊燈)記得持有期間的最高點。同一個物件先跑訓練段再跑驗證段,
    狀態會從前一段漏進來 —— 驗證段就不再是乾淨的樣本外。
    """
    hyps = _hunt().hypotheses({"BTC-USDT": [(0, 0.0)]})
    for key, _, make in hyps:
        assert callable(make), key
        assert make() is not make(), f"{key} 兩次拿到同一個物件"


def test_the_correction_denominator_follows_the_hypothesis_count():
    """**分母跟著假說數走,不是各批算各批。**

    同一批資料、同一個決定,試過幾次就付幾次的帳。分批算就是在
    偷偷放寬門檻 —— 而那是最難被抓到的一種作弊。
    """
    src = (ROOT / "scripts/hunt.py").read_text(encoding="utf-8")
    assert "trials = len(rows)" in src, "分母必須是實際跑過的假說數"
    assert "分母跟著變成 18" in src


def test_no_parameter_is_searched():
    """參數一律用各自領域的通行預設值。

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


def test_b2_is_pulled_out_when_funding_does_not_reach_the_training_segment():
    """**不同期間的兩個數字不可以並排。**

    2026-09-25:快取修好之後 B2 第一次跑出數字 —— 訓練 0.00、
    驗證 1.07、回撤 3.2%。1.07 是現役 0.51 的兩倍多,回撤全場最低。
    看起來像突破,實際上:交易所的費率歷史只有 333 天,訓練段
    (2023-05 ~ 2025-08)一筆都沒有,所以 B2 整個訓練段空手。
    那個 1.07 只算了驗證段裡有資料的那一截,現役的 0.51 是整段。

    這跟「主城 12 天 +4.24% 對測試組 1 天 -0.03%」是同一種錯,
    只是這次藏在資料涵蓋範圍裡,不在起算日裡。
    """
    from datetime import datetime, timedelta, timezone
    h = _hunt()
    d0 = datetime(2023, 1, 1, tzinfo=timezone.utc)
    dates = [d0 + timedelta(days=i) for i in range(1000)]

    # 費率只涵蓋最後 200 天
    late = int(dates[800].timestamp() * 1000)
    assert h.funding_covers({"BTC-USDT": [(late, 0.0)]}, dates) == 800
    # 完全沒有資料 → None
    assert h.funding_covers({}, dates) is None
    # 涵蓋整段 → 第 0 格
    early = int(dates[0].timestamp() * 1000)
    assert h.funding_covers({"BTC-USDT": [(early, 0.0)]}, dates) == 0


def test_the_b2_side_report_refuses_to_call_it_a_pass_or_a_fail():
    """抽出來單獨看 ≠ 給它一條比較寬鬆的路。

    單獨那一段只有一個市場週期,而且它**就是**現役策略被挑出來
    之後的那一段。那種條件下的漂亮數字,誰都做得出來。
    """
    src = (ROOT / "scripts/hunt.py").read_text(encoding="utf-8")
    assert "這不是通過,也不是沒通過 —— 是還不能判" in src
    assert "不進下面那張表" in src
    assert "所以第一關**不是輸," in src and "是沒得比**" in src
