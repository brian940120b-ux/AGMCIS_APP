"""
狀態條 —— 一眼看出正不正常 · 2026-09-20

執政官:「面板上我只想看得到數據,不想要一堆文字,
然後幫我確認現在是否正常。」

═══ 一個永遠綠燈的狀態列比沒有狀態列危險 ═══
它讓上面每一層都變綠,而綠的理由是它什麼都沒在看(教訓第 5 條)。
所以這一組守兩件事:

一、每一項都要有**實際量測來源**,不准放「系統健康」這種抽象項目
二、狀態檢查**不准弄壞它檢查的東西**
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scripts.dashboard as dash


def test_the_status_check_does_not_poison_the_cache_it_reads():
    """**這是我第一版寫出來的 bug。**

    第一版用 `_cached("exchange", 60, lambda: {"error": "未取"})` 去讀 ——
    快取冷的時候那個 lambda 會被呼叫,把一個**假的錯誤**寫進共用的
    快取鍵,而交易所那張卡接著就會拿到它,整張變成「問不到」。

    一個狀態檢查把它檢查的東西弄壞,是最糟的一種檢查。
    """
    dash._CACHE.clear()
    dash.status_checks()
    assert dash._CACHE.get("exchange") is None, \
        "狀態檢查把 exchange 快取寫髒了"


def test_being_flat_is_not_reported_as_a_fault():
    """空手是一個合法的部位,而且這個策略常常空手。

    把它算成紅燈,狀態列就會在**最正常的時候**喊異常 ——
    而一個會亂喊的警報,久了就會被當成背景雜訊忽略。
    """
    import time as _t
    from portfolio.account import Account
    from portfolio.scorecard import Live
    dash._CACHE["sim"] = (_t.time(), {
        "live": Live(), "card": None, "age_s": None, "source": "無持倉",
        "marks": {}})
    got = dict((n, ok) for n, ok, _ in dash.status_checks())
    assert got.get("行情") is True, "空手被當成故障了"


def test_every_check_carries_a_measurable_detail():
    """不准出現只有名字沒有數字的項目 —— 那種項目永遠是綠的。"""
    for name, ok, detail in dash.status_checks():
        assert name and detail, f"{name} 沒有可量測的細節"


def test_the_verdict_is_driven_by_the_checks_not_written_by_hand():
    """『正常』這兩個字必須是算出來的。"""
    src = Path(dash.__file__).read_text(encoding="utf-8")
    body = src[src.index("def block_status("):src.index("def block_tickets(")]
    assert "status_checks()" in body
    assert 'word = "正常" if not bad' in body


# ══════════════════════════════════════════════════════════
# 說明摺起來,但**警語不摺**
# ══════════════════════════════════════════════════════════
def test_explanations_are_collapsed_but_warnings_stay_visible():
    """摺掉警告等於關掉警告。

    `.flag.warn`(有東西不對)與 `.flag.ok`(這件事確認過了)
    本來就該一眼看到,不能跟說明一起被摺進去。
    """
    page = dash.render()
    assert '<details class="ex">' in page, "說明沒有被摺起來"
    import re
    folded = re.findall(r'<details class="ex">(.*?)</details>', page, re.S)
    for body in folded:
        assert "flag warn" not in body, "警語被摺進說明裡了"
        assert "flag ok" not in body, "確認過的結論被摺進說明裡了"


def test_short_notes_are_left_alone():
    """太短的摺起來反而多一行。"""
    out = dash._collapse_prose('<p class="note">很短</p>')
    assert "<details" not in out
