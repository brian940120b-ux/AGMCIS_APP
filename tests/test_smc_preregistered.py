"""SMC 的**翻譯表**與驗收條件,在跑之前就要釘死 · 2026-09-25

═══ 這組測試守的是這個專案裡最微妙的一種作弊 ═══
SMC 是憑眼睛畫的方法。要檢驗它,得先把它翻譯成規則,而**翻譯本身
就是選擇**:訂單塊怎麼定義、分形要幾根、失衡區算不算填補完畢,
每一個都有好幾種講法。

如果先跑、看了結果再回頭調整定義,那就是在搜參數 —— 而且是在
最不容易被抓到的地方搜,因為每一次調整聽起來都像
「我只是把 SMC 定義得更正確」。

所以規矩是:翻譯表先寫死、先提交,才准跑第一次。這組測試就是
那個提交的憑證。**它們擋的不是程式錯,是我自己事後改口。**
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SMC = (ROOT / "portfolio/smc.py").read_text(encoding="utf-8")
HUNT = (ROOT / "scripts/smc_hunt.py").read_text(encoding="utf-8")


def test_the_translation_table_names_its_source_and_its_date():
    """來源、日期、以及「跑完不准改」這句話,都要在檔案裡。"""
    assert "1336cryptoclub" in SMC, "沒有記錄來源"
    assert "AI 內容" in SMC, "沒有記錄帳號自標 AI 內容這件事"
    assert "2026-09-25" in SMC
    assert "跑完不管結果好壞,定義一個字都不准改" in SMC


def test_every_step_of_the_post_has_a_mechanical_definition():
    """貼文四步驟 + 風控,每一項都要有對應的函式。"""
    from portfolio import smc
    for name in ("prev_period_extremes", "breaks", "order_block",
                 "fvg", "engulfing", "setups"):
        assert hasattr(smc, name), name


def test_the_only_parameter_is_the_fractal_and_it_is_the_textbook_default():
    """全套只有一個參數,而且是教科書預設值 —— **不搜**。"""
    from portfolio import smc
    assert smc.FRACTAL_K == 2
    assert "全套唯一一個參數" in SMC and "不搜" in SMC


def test_swings_carry_the_bar_they_can_first_be_known_at():
    """前視偏誤的防線靠型別,不靠註解。

    擺動點是回頭看才知道的。沒有 confirmed_at,回測會漂亮得
    不像話而且不會報錯 —— donchian 當初就栽在同一個坑。
    """
    from portfolio.smc import Swing
    assert "confirmed_at" in Swing.__dataclass_fields__
    src = SMC[SMC.index("def breaks("):SMC.index("def order_block(")]
    assert "confirmed_at <= i" in src, "breaks 沒有檢查確認時點"


def test_the_ladders_are_fixed_at_four():
    """四套,寫死。跑完之後多加一套 = 用結果挑假說。"""
    tree = ast.parse(HUNT)
    node = next(n for n in tree.body
                if isinstance(n, ast.Assign)
                and getattr(n.targets[0], "id", "") == "LADDERS")
    assert len(node.value.elts) == 4
    assert f"Bonferroni ×{len(node.value.elts)}" in HUNT


def test_the_acceptance_criteria_are_in_the_file_before_any_result():
    for phrase in ("樣本 >= 100 筆", "贏過**隨機對照組的第 95 百分位**",
                   "前半段與後半段", "Bonferroni ×4",
                   "沒過就蓋棺"):
        assert phrase in HUNT, phrase


def test_there_is_a_random_control_not_just_a_comparison_with_zero():
    """固定 R:R 拉大的話,亂進場也會有漂亮的盈虧比。

    所以「平均 R > 0」不算數 —— 要贏的是**同樣停損止盈距離、
    隨機進場時點**的那一組。這是型態類策略唯一誠實的對照。
    """
    from portfolio import event_sim
    assert hasattr(event_sim, "control")
    assert hasattr(event_sim, "beats_control")
    assert "beats_control" in HUNT


def test_the_simulator_never_assumes_the_favourable_side_of_a_bar():
    """同根同時碰到停損與止盈 → 算停損;跳空 → 成交在開盤價。

    兩條都是**對策略不利**的假設。反過來的話,任何固定 R:R
    的策略都會憑空變好看,而且不會報錯。
    """
    src = (ROOT / "portfolio/event_sim.py").read_text(encoding="utf-8")
    assert "一律算停損" in src
    assert "成交在開盤價,不在停損價" in src


def test_the_post_made_no_evidence_claim_and_we_say_so():
    """貼文沒附樣本數、期間或績效。那不代表它錯 —— 但要寫下來。"""
    assert "未附任何績效證據" in HUNT


def test_the_universe_size_was_fixed_before_the_run_and_says_why():
    """幣種數量也是預先登記的一部分。

    30 這個數字是 2026-09-25 用**合成資料**乾跑管線之後定的:
    三個幣只生出 14~33 筆,撐不到 100 筆的門檻。理由是統計檢定力,
    **不是**「換一批幣看看會不會比較好看」—— 兩者的差別就是
    預先登記的全部意義。
    """
    import scripts.smc_hunt as h                       # noqa: PLC0415
    assert h.UNIVERSE_N == 30
    assert "在跑之前決定,理由是**樣本量**不是結果" in HUNT
    assert "跑完之後**不准**再動這個數字" in HUNT


def test_a_short_sample_reports_the_count_and_refuses_a_verdict():
    """樣本 < 100 時只報樣本數。

    樣本不足時給出的勝率或報酬是雜訊的形狀,不是方法的形狀 ——
    而那種數字最容易被當成結論記住。
    """
    import scripts.smc_hunt as h                       # noqa: PLC0415
    assert h.MIN_TRADES == 100
    assert "只報樣本數,不報結論" in HUNT
