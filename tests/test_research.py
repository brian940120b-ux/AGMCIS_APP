"""
研究迴路 —— 系統可以不停地試,但不能自己生效 · 2026-09-13

這一組守的是**過擬合**,而過擬合不會自己現形:它長得像一個好消息。

一、試 32 次,最好的那次「看起來贏」幾乎是必然的 —— 要校正
二、只贏訓練段是過擬合的典型形狀 —— 驗證段也要贏
三、差不多好就不換
四、提案不會自己生效,而且裁決過的不得被覆蓋
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio import research as R

INC = R.Variant(ma=50, vol_target_pct=27.0, leverage_cap=3.0)
CHA = R.Variant(ma=100, vol_target_pct=20.0, leverage_cap=3.0)
TODAY = "2026-09-13T00:00:00Z"


def m(calmar, dd=10.0):
    return {"calmar": calmar, "max_dd_pct": dd}


def judged(**over):
    kw = dict(incumbent=INC, challenger=CHA, trials=31,
              train=m(1.80), test=m(1.60),
              incumbent_train=m(1.40), incumbent_test=m(1.33),
              p_raw=0.0005, as_of=TODAY)
    kw.update(over)
    return R.judge_challenger(**kw)


# ══════════════════════════════════════════════════════════
# 一、網格
# ══════════════════════════════════════════════════════════
def test_the_grid_is_small_and_the_incumbent_is_not_a_trial():
    """網格的每一次變大都必須是**刻意的**,而且要在這裡登記。

    2026-09-18 加了多空族(執政官問「除了做多有做空嗎」),
    網格從 47 變成 79。這條測試當場變紅 —— 那正是它的用途:
    舊系統測了 1391 個配置,實際通過 15 案而雜訊預期 52 案,
    也就是**搜尋本身在製造假陽性**。網格悄悄長大就是那條路。

    為什麼這一次的變大是可接受的:
      · 多空族**沒有新增任何自由參數** —— 它用的就是同一組 MA_GRID,
        同一組波動目標。它不是把參數空間撐大,是對同一組參數
        問一個結構性的問題:「跌破的時候空手,還是做空?」
      · Bonferroni 乘的是**有效**試驗數(行為去重後),多空族因此
        照樣要付校正的代價 —— 加進來只會讓門檻更高,不會更鬆。

    上限留在 96:再往上就必須先回答「這些是新假說,還是在掃參數」。
    """
    g = R.grid(INC)
    combos = len(R.VOL_GRID) * len(R.LEV_GRID)
    per_combo = len(R.MA_GRID) + len(R.BREAKOUT_GRID) + len(R.MA_GRID)
    assert len(g) == combos * per_combo - 1
    assert INC not in g, "現任者是被挑戰的對象,不算一次試驗"
    assert len(g) < 96, "網格一旦變大,這支就是過擬合機器"


def test_breakout_is_a_challenger_not_a_special_case():
    """2026-09-18 執政官問「不是有支撐壓力、突破假突破、回測去判斷嗎」。

    答案是可以 —— 但它得跟均線**在同一組閘門下比**,不是因為它聽起來
    比較像「真正的技術分析」就給它特權。
    """
    kinds = {v.kind for v in R.grid(INC)}
    assert kinds == {"ma", "breakout", "longshort"}
    bo = [v for v in R.grid(INC) if v.kind == "breakout"]
    assert {(v.ma, v.exit_n) for v in bo} == set(R.BREAKOUT_GRID)


def test_long_short_is_a_challenger_too_and_adds_no_new_parameters():
    """2026-09-18 執政官問「除了做多有做空嗎」。

    答案:現役策略只做多,而多空版的程式碼 2026-09-08 就寫好了,
    附了完整的預先登記理由,然後**沒有任何地方呼叫它**。

    接上的方式是讓它當挑戰者,不是直接開啟 —— 做空改變的是風險的
    形狀。而它用的是**同一組 MA_GRID**:唯一的差別在「跌破」那一邊,
    現任空手,它做空。沒有新參數,所以這是最乾淨的一次對照。
    """
    ls = [v for v in R.grid(INC) if v.kind == "longshort"]
    assert {v.ma for v in ls} == set(R.MA_GRID), "多空族該用同一組均線長度"
    assert all(v.exit_n == 0 for v in ls), "多空版不該有出場參數"
    v = R.Variant(ma=50, vol_target_pct=27.0, leverage_cap=3.0,
                  kind="longshort")
    assert v.key.startswith("ls50")
    assert "做空" in v.describe()


def test_the_breakout_values_are_textbook_not_searched():
    """20/10 與 55/20 是海龜系統一與系統二。

    **搜出來的區間會把搜尋空間撐大**,而撐大的代價是校正變嚴、
    或者更糟 —— 沒人注意到它撐大了。
    """
    assert R.BREAKOUT_GRID == ((20, 10), (55, 20))


def test_a_breakout_variant_describes_itself_in_plain_words():
    v = R.Variant(ma=20, vol_target_pct=27.0, leverage_cap=3.0,
                  kind="breakout", exit_n=10)
    assert "突破 20 日高" in v.describe()
    assert "跌破 10 日低" in v.describe()
    assert v.key.startswith("bo20-10")


def test_the_grid_never_tries_more_leverage_than_the_incumbent():
    """2026-09-10 的 20× 真的爆了。槓桿只往下試。"""
    assert max(R.LEV_GRID) <= INC.leverage_cap


# ══════════════════════════════════════════════════════════
# 二、多重比較校正 —— 這是整支最重要的一條
# ══════════════════════════════════════════════════════════
def test_the_p_value_is_multiplied_by_the_number_of_trials():
    assert R.bonferroni(0.01, 31) == pytest.approx(0.31)
    assert R.bonferroni(0.5, 31) == 1.0          # 封頂
    assert R.bonferroni(None, 31) is None        # 算不出來就不給數字


def test_a_result_that_only_looks_good_because_of_many_trials_is_refused():
    """p=0.01 聽起來很顯著 —— 但試了 31 次之後它是 0.31。"""
    p = judged(p_raw=0.01)
    assert p.passed is False
    assert any("多重比較校正" in n and not ok for n, ok, _ in p.checks)


def test_a_genuinely_strong_result_survives_the_correction():
    assert judged(p_raw=0.0005).passed is True


def test_no_p_value_means_no_proposal():
    """算不出 p 就不是提案。**沒有證據不等於沒有問題。**"""
    assert judged(p_raw=None).passed is False


# ══════════════════════════════════════════════════════════
# 三、訓練段 / 驗證段
# ══════════════════════════════════════════════════════════
def test_winning_only_the_training_segment_is_refused():
    """**過擬合的典型形狀。**"""
    p = judged(train=m(2.50), test=m(1.10))
    assert p.passed is False
    assert any("驗證段" in n and not ok for n, ok, _ in p.checks)


def test_breaching_the_drawdown_contract_is_refused_even_if_it_wins():
    """贏了現任但超過契約 —— **仍然不可交易**。"""
    p = judged(test=m(2.0, dd=22.0))
    assert p.passed is False
    assert any("回撤" in n and not ok for n, ok, _ in p.checks)


def test_a_tiny_edge_is_not_worth_switching():
    """差不多好就不換 —— 換參數本身有成本。"""
    p = judged(test=m(1.35))                     # 現任 1.33,只多 0.02
    assert p.passed is False
    assert any("優勢" in n and not ok for n, ok, _ in p.checks)


def test_every_gate_is_recorded_including_the_ones_that_passed():
    """「你憑什麼」必須逐條回答得出來。"""
    names = [n for n, _, _ in judged().checks]
    assert len(names) == 5
    assert all(d for _, _, d in judged().checks), "每一關都要有說明"


# ══════════════════════════════════════════════════════════
# 四、block bootstrap
# ══════════════════════════════════════════════════════════
def test_a_challenger_that_never_wins_gets_p_one():
    rng = random.Random(1)
    inc = [rng.gauss(0.001, 0.02) for _ in range(400)]
    cha = [x - 0.002 for x in inc]
    assert R.calmar_bootstrap(cha, inc, paths=200) == 1.0


def test_a_clearly_better_challenger_gets_a_small_p():
    rng = random.Random(2)
    inc = [rng.gauss(0.0005, 0.02) for _ in range(800)]
    cha = [r * 0.5 + 0.0006 for r in inc]      # 一半波動、略高報酬
    p = R.calmar_bootstrap(cha, inc, paths=300)
    assert p is not None and p < 0.05


def test_the_test_is_on_calmar_because_that_is_what_we_select_on():
    """**用 A 挑、用 B 檢定的流程,檢定不到它挑的東西。**

    2026-09-18 實測抓到:100 日均線的驗證段 Calmar 0.65 > 現任 0.48,
    **而它的平均日報酬比現任低** —— 它贏在回撤小。
    舊版 bootstrap 平均日報酬,對它回 p=1.0(「根本沒贏」),
    於是一個靠降低回撤取勝的挑戰者**永遠**過不了。
    """
    rng = random.Random(7)
    inc = [rng.gauss(0.0012, 0.03) for _ in range(800)]
    cha = [r * 0.35 + 0.0004 for r in inc]      # 報酬低很多,但回撤小很多

    assert sum(cha) / len(cha) < sum(inc) / len(inc), "挑戰者報酬確實較低"
    assert R._calmar_of(cha) > R._calmar_of(inc), "但 Calmar 較高"
    assert R.calmar_bootstrap(cha, inc, paths=300) < 0.5, \
        "檢定要看得到這個優勢 —— 舊版看不到"


def test_too_short_a_sample_gives_no_number_rather_than_a_bad_one():
    assert R.calmar_bootstrap([0.01] * 10, [0.0] * 10) is None


# ══════════════════════════════════════════════════════════
# 有效試驗次數
# ══════════════════════════════════════════════════════════
def test_duplicate_variants_count_as_one_trial():
    """**乘名目次數 = 無中生有地加嚴校正。**

    2026-09-18 實測:波動目標 15~35% 時總曝險碰不到槓桿上限,
    所以 lev2 與 lev3 每一組的訓練/驗證/回撤完全一樣 ——
    47 種裡大約一半是同一個東西換個名字。
    """
    same = (m(1.2), m(0.6))
    other = (m(1.5), m(0.7))
    assert R.effective_trials([same, same, same]) == 1
    assert R.effective_trials([same, other]) == 2
    assert R.effective_trials([]) == 1, "至少算一次,不准回 0"


def test_effective_trials_looks_at_behaviour_not_names():
    a = ({"calmar": 1.2000001}, {"calmar": 0.6, "max_dd_pct": 10.0})
    b = ({"calmar": 1.2000002}, {"calmar": 0.6, "max_dd_pct": 10.0})
    assert R.effective_trials([a, b]) == 1


# ══════════════════════════════════════════════════════════
# 五、提案不會自己生效
# ══════════════════════════════════════════════════════════
def test_a_failing_proposal_cannot_be_accepted(tmp_path):
    store = tmp_path / "p.json"
    bad = judged(p_raw=0.02)
    R.save([bad], store)
    with pytest.raises(ValueError) as e:
        R.decide(bad.proposal_id, "accepted", path=store)
    assert "沒有通過全部關卡" in str(e.value)


def test_accepting_records_who_and_when_and_does_not_change_settings(tmp_path):
    store = tmp_path / "p.json"
    good = judged()
    R.save([good], store)
    out = R.decide(good.proposal_id, "accepted", by="執政官", path=store)
    assert out.status == "accepted"
    assert out.decided_by == "執政官" and out.decided_on
    # **這一步不套用任何設定** —— 只記錄決定
    from exchange.bingx.trade import BACKSTOP_PCT
    assert BACKSTOP_PCT == 25.0


def test_a_decided_proposal_cannot_be_silently_overwritten(tmp_path):
    store = tmp_path / "p.json"
    good = judged()
    R.save([good], store)
    R.decide(good.proposal_id, "rejected", path=store)
    with pytest.raises(R.AlreadyDecided):
        R.decide(good.proposal_id, "accepted", path=store)


def test_rerunning_research_does_not_wipe_a_decision(tmp_path):
    """今天核可、明天重跑,那個決定不能無聲消失。"""
    store = tmp_path / "p.json"
    good = judged()
    R.save([good], store)
    R.decide(good.proposal_id, "rejected", path=store)

    merged = R.merge(R.load(store), [judged()])   # 同一個 id 重跑
    assert len(merged) == 1
    assert merged[0].status == "rejected"


def test_the_same_comparison_on_the_same_day_is_the_same_proposal():
    """否則每天跑一次就堆出一百個一樣的提案,而人會停止讀它們。"""
    assert judged().proposal_id == judged().proposal_id
    assert (R.proposal_id(INC, CHA, TODAY)
            != R.proposal_id(INC, CHA, "2026-09-14T00:00:00Z"))


def test_the_trial_count_is_carried_on_the_proposal(tmp_path):
    """**沒有它,「我們找到更好的了」這句話沒有意義。**"""
    store = tmp_path / "p.json"
    R.save([judged(trials=31)], store)
    assert R.load(store)[0].trials == 31


# ══════════════════════════════════════════════════════════
# 證據不能自相矛盾
# ══════════════════════════════════════════════════════════
def _detail(p, name):
    return next(d for n, _, d in p.checks if n == name)


def test_a_passing_drawdown_check_does_not_say_it_failed():
    """2026-09-18 實跑抓到的:

        ✓ 回撤在契約內
            最大回撤 12.7% vs 契約 15%(贏了現任但超過契約,仍然不可交易)

    那句「超過契約,仍然不可交易」原本是**無條件**印的,於是一個
    明明通過的關卡,旁邊跟著一句說它沒過。

    一行自相矛盾的證據比沒有證據糟 —— 讀的人會開始不信任整張表,
    而這張表的全部用處就是可以被相信。
    """
    d = _detail(judged(train=m(1.80, dd=12.7), test=m(1.60, dd=8.8)),
                "回撤在契約內")
    assert "12.7%" in d
    assert "不可交易" not in d, f"通過的關卡卻說不可交易:{d}"


def test_a_failing_drawdown_check_still_explains_why_it_matters():
    """沒過的時候那句話要在 —— 贏了現任也不能放寬契約。"""
    d = _detail(judged(train=m(1.80, dd=22.0), test=m(1.60, dd=8.8)),
                "回撤在契約內")
    assert "22.0%" in d
    assert "不可交易" in d and "不因為贏了而放寬" in d


def test_the_validation_gate_admits_it_is_nearly_tautological():
    """挑戰者是用**驗證段 Calmar 排序**挑出來的,所以被挑中的那個
    當然在驗證段好看 —— 它就是因為好看才被挑中的。

    這一關過了幾乎不帶資訊,而真正替它付帳的是 Bonferroni。
    不講的話,一個讀表的人會把「✓ 驗證段也贏過現任」當成獨立證據,
    然後在心裡把校正後的 p=1.0 打個折扣 —— 那正好是反過來的。
    """
    d = _detail(judged(), "驗證段也贏過現任")
    assert "按驗證段 Calmar 挑出來的" in d
    assert "多重比較校正" in d


def test_the_selection_really_is_by_validation_calmar():
    """上面那句話必須跟程式一致 —— 排序改了而說明沒改,就是一句
    看起來有根據的錯話。"""
    from pathlib import Path as _P
    src = (_P(R.__file__).resolve().parents[1]
           / "scripts/research.py").read_text(encoding="utf-8")
    assert 'rows.sort(key=lambda r: (r[2].get("calmar") or -99), reverse=True)' \
        in src, "挑選方式變了,judge_challenger 裡的說明要跟著改"
