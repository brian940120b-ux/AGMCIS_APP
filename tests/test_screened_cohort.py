"""
測試組復活 —— 動態交易池 · 2026-09-19

執政官:「有一種篩選機制,其他人都做得出來,為什麼你做不出來?」

**做出來過,壞掉了,被退役,而我沒把它接回來。**

2026-09-13:測試組篩進 1000PEPE-USDT,而沒有人抓過那個幣的資金費
歷史 -> SpecMissing -> **整個 tick 死掉,73.7 小時沒有記帳**。

那個例外是對的。錯的有兩件:
  一、篩選只問「這個幣值不值得交易」,沒問「我有沒有資格替它記帳」
  二、一個幣的資料不全,**整本帳跟著死**

這一組把那兩件釘住。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio import paper


# ══════════════════════════════════════════════════════════
# 一、資料不全的幣不准進交易池
# ══════════════════════════════════════════════════════════
def test_a_symbol_without_funding_history_is_dropped_not_fatal(monkeypatch):
    """**這一條就是那 73.7 小時。**

    1000PEPE 通過了流動性與歷史長度,但抓不到資金費歷史。
    正確的處置是**把它排除**,不是讓整本帳死掉。
    """
    monkeypatch.setattr(paper, "SYMBOLS", ["BTC-USDT"], raising=False)

    import portfolio.universe as uni
    monkeypatch.setattr(uni, "screen",
                        lambda **kw: ["BTC-USDT", "1000PEPE-USDT"])

    import portfolio.specs as specs

    def fake_refresh(syms, **kw):
        if "1000PEPE-USDT" in syms:
            raise specs.SpecMissing("沒有 1000PEPE-USDT 的資金費率歷史")
        return 1
    monkeypatch.setattr(specs, "refresh_funding", fake_refresh)

    got = paper.screened_universe()
    assert got == ["BTC-USDT"], f"資料不全的幣沒被排掉:{got}"


def test_an_empty_screen_falls_back_instead_of_killing_the_tick(monkeypatch):
    """挑不到東西**不能讓記帳停掉**。

    一個少了幾個幣的交易池,比一本死掉三天的帳好得多。
    但退回預設值**必須留下紀錄** —— 一個安靜退回的篩選,
    比沒有篩選更難發現。
    """
    import portfolio.specs as specs
    import portfolio.universe as uni
    monkeypatch.setattr(uni, "screen", lambda **kw: [])
    monkeypatch.setattr(specs, "refresh_funding", lambda s, **kw: 1)

    got = paper.screened_universe()
    assert got == list(paper.SYMBOLS), "挑不到就該退回主城名單"


def test_the_screen_asks_whether_we_can_account_for_the_coin():
    """前三關問「這個幣值不值得交易」,第四關問「我有沒有資格替它記帳」
    —— 兩者不是同一件事,而混在一起的代價是三天沒有帳本。"""
    src = Path(paper.__file__).read_text(encoding="utf-8")
    body = src[src.index("def screened_universe("):src.index("SCREENED = Config(")]
    assert "refresh_funding" in body
    assert "73.7" in src or "1000PEPE" in src


# ══════════════════════════════════════════════════════════
# 二、測試組是平行的,不是取代
# ══════════════════════════════════════════════════════════
def test_the_cohort_has_its_own_books_and_never_touches_the_main_ones():
    """**獨立帳本。** 共用一份 plan()/tick(),但三個檔案全部分開 ——
    寫到主城的帳本上會把十天的前向樣本毀掉。"""
    for attr in ("state_path", "curve_path", "risk_path"):
        a = getattr(paper.MAIN, attr)
        b = getattr(paper.SCREENED, attr)
        assert a != b, f"{attr} 跟主城共用同一個檔案"


def test_the_cohort_differs_from_main_only_by_its_universe():
    """憲法鐵則一:不要有第二份實作。

    測試組跟主城唯一的差別是交易池 —— 策略、波動目標、槓桿上限、
    回看期全部一樣。有第二個差異,就分不出是哪一個造成的。
    """
    for attr in ("strategy", "vol_target_pct", "vol_lookback",
                 "leverage_cap", "lookback_days"):
        assert getattr(paper.MAIN, attr) == getattr(paper.SCREENED, attr), \
            f"{attr} 兩邊不一樣 —— 那就比不出交易池的效果了"
    assert paper.MAIN.universe_fn is None
    assert paper.SCREENED.universe_fn is not None


def test_main_still_uses_the_fixed_seven():
    """**主城不會被這次改動碰到。** 動態交易池好不好由前向資料回答,
    在那之前主城照舊 —— 換掉主城會讓十天的樣本歸零。"""
    assert paper.MAIN.universe_fn is None
    assert len(paper.MAIN.symbols) == 7


# ══════════════════════════════════════════════════════════
# 三、測試組爆炸不准拖垮主城
# ══════════════════════════════════════════════════════════
def test_the_daily_job_runs_the_cohort_after_main_and_isolates_failures():
    """一個實驗把本業弄掛掉,是最蠢的一種當機。"""
    src = (Path(paper.__file__).resolve().parents[1]
           / "scripts/daily.py").read_text(encoding="utf-8")
    main_at = src.index("r = tick()")
    cohort_at = src.index("tick(cfg=SCREENED)")
    assert main_at < cohort_at, "測試組必須排在主城**之後**"
    tail = src[cohort_at - 900:cohort_at + 700]
    assert "不影響主城" in tail or "主城不受影響" in tail


def test_tick_reports_which_universe_it_traded():
    """交易池的大小本身就是「篩選有沒有在動」的證據。"""
    src = Path(paper.__file__).read_text(encoding="utf-8")
    assert '"symbols": list(p.get("symbols") or [])' in src


# ══════════════════════════════════════════════════════════
# 兩組要從同一天起算 · 2026-09-20
# ══════════════════════════════════════════════════════════
#
# 2026-09-20 的實際數字:
#   主城   12 天  +4.24%   ← 從 09-08
#   測試組  1 天  -0.03%   ← 從 09-19
#
# 並排放著,任何人都會去比。而那個比較**比的是起跑時間,不是選幣** ——
# 主城那 4.24% 裡有 11 天是測試組還不存在的時候賺的。

def _curve(tmp_path, name, rows):
    import json
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(
        {"signal_day": d, "equity": e}) for d, e in rows), encoding="utf-8")
    return p


def test_both_cohorts_are_measured_from_the_day_they_overlap(tmp_path):
    """主城全期 +5%,但**同期間**只有 +0.96% —— 那才是能比的數字。"""
    from portfolio.scorecard import since
    a = _curve(tmp_path, "a.jsonl", [("2026-09-08", 10000),
                                     ("2026-09-18", 10300),
                                     ("2026-09-19", 10400),
                                     ("2026-09-20", 10500)])
    b = _curve(tmp_path, "b.jsonl", [("2026-09-19", 10000),
                                     ("2026-09-20", 10120)])
    start, main_pct, cohort_pct, n = since(a, b)
    assert start == "2026-09-19"
    assert round(main_pct, 2) == 0.96, "主城該用同期間算,不是全期"
    assert round(cohort_pct, 2) == 1.20
    assert n == 2


def test_one_overlapping_day_is_refused_rather_than_shown_as_zero(tmp_path):
    """重疊一天沒有「期間」可言。**回 None,不是回 0%** ——
    0% 看起來像一個結論,而那裡根本還沒有結論。"""
    from portfolio.scorecard import since
    a = _curve(tmp_path, "a.jsonl", [("2026-09-08", 10000),
                                     ("2026-09-19", 10400)])
    b = _curve(tmp_path, "b.jsonl", [("2026-09-19", 10000)])
    start, main_pct, cohort_pct, n = since(a, b)
    assert (main_pct, cohort_pct) == (None, None)
    assert n == 1


def test_the_card_refuses_to_print_two_returns_it_cannot_compare():
    """**不顯示一個看起來能比的數字。**

    這是 2026-09-20 當下的真實狀態:主城 12 天、測試組 1 天,
    重疊不到兩天。那時候唯一誠實的話是「還沒得比」。
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1]
           / "scripts/dashboard.py").read_text(encoding="utf-8")
    body = src[src.index("def _cohort_row("):src.index("def _sim_rows(")]
    assert "還沒得比" in body
    assert "比的是起跑時間,不是選幣" in body
    assert "since(" in body
