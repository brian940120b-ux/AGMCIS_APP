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
