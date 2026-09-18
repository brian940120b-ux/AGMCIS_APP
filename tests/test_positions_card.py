"""
模擬持倉卡 · 2026-09-18

執政官:「我也希望看到他買了什麼,即時盈虧像之前那樣。」

這張卡有兩個特別容易出事的地方,而兩個都跟「不知道」有關:

一、**問不到現價的時候不准印 0。** 盈虧 0 和「不知道盈虧」在畫面上
    長得一樣,但意思完全相反 —— 一個是沒賺沒賠,一個是這檔的死活
    現在無人知曉。合計也一樣:少算一檔的合計是**偏少的**,不是完整的。
二、**不准讓人以為這是他的錢。** 這是模擬帳戶的倉,使用者的交易所
    帳戶是空的。兩者的差距就是「指令單」那一塊。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scripts.dashboard as dash
from portfolio.account import Account, Position


def seed(held, marks, missing=()):
    """把資料塞進卡片的快取,直接測渲染那一段。"""
    a = Account()
    a.positions = dict(held)
    a.realized_pnl = 12.5
    dash._CACHE["positions"] = (time.time(), {
        "a": a, "held": dict(held), "marks": dict(marks),
        "missing": list(missing)})
    return dash.block_positions()


def pos(sym, amt, avg, lev=3.0):
    return Position(symbol=sym, position_amt=amt, avg_price=avg, leverage=lev)


# ══════════════════════════════════════════════════════════
# 一、它印得出來,而且印的是對的數字
# ══════════════════════════════════════════════════════════
def test_a_long_in_profit_shows_the_gain():
    html = seed({"BTC-USDT": pos("BTC-USDT", 0.1, 70000.0)},
                {"BTC-USDT": 76000.0})
    assert "BTCUSDT" in html
    assert "+600.00" in html, "0.1 顆 × (76000-70000) = +600"
    assert "多" in html


def test_a_long_in_loss_shows_the_loss():
    html = seed({"BTC-USDT": pos("BTC-USDT", 0.1, 76000.0)},
                {"BTC-USDT": 70000.0})
    assert "-600.00" in html


def test_the_roi_is_against_the_margin_not_the_notional():
    """本金 = 名目 ÷ 槓桿。ROI 用本金當分母 —— 用名目會讓 3 倍槓桿的
    賺賠看起來只有實際的三分之一,而那是在騙自己風險比較小。"""
    html = seed({"BTC-USDT": pos("BTC-USDT", 0.1, 70000.0, lev=3.0)},
                {"BTC-USDT": 76000.0})
    # 本金 = 0.1 × 70000 / 3 = 2333.33;600 / 2333.33 = 25.7%
    assert "+25.7% 本金" in html


def test_a_short_is_labelled_short_and_profits_when_price_falls():
    html = seed({"ETH-USDT": pos("ETH-USDT", -1.0, 3000.0)},
                {"ETH-USDT": 2800.0})
    assert "空" in html
    assert "+200.00" in html


# ══════════════════════════════════════════════════════════
# 二、不知道就說不知道
# ══════════════════════════════════════════════════════════
def test_a_missing_price_is_not_printed_as_zero():
    """**盈虧 0 和「不知道盈虧」在畫面上長得一樣,意思相反。**"""
    html = seed({"BTC-USDT": pos("BTC-USDT", 0.1, 70000.0)}, {},
                missing=["BTC-USDT"])
    assert "問不到現價" in html
    assert "不是 0,是不知道" in html


def test_a_partial_total_says_it_is_short_of_complete():
    """少算一檔的合計是**偏少的** —— 不講的話它看起來就是全部。"""
    html = seed({"BTC-USDT": pos("BTC-USDT", 0.1, 70000.0),
                 "SOL-USDT": pos("SOL-USDT", 10.0, 200.0)},
                {"BTC-USDT": 76000.0}, missing=["SOL-USDT"])
    assert "SOL-USDT" in html
    assert "合計因此是偏少的" in html


def test_an_empty_account_says_flat_is_a_position():
    """空手是一個部位,不是「沒在跑」—— 那兩件事的處置完全不同。"""
    html = seed({}, {})
    assert "空手" in html
    assert "空手是一個部位" in html


# ══════════════════════════════════════════════════════════
# 三、不准讓人以為這是他的錢
# ══════════════════════════════════════════════════════════
def test_the_card_says_this_is_the_simulation_not_the_exchange():
    html = seed({"BTC-USDT": pos("BTC-USDT", 0.1, 70000.0)},
                {"BTC-USDT": 76000.0})
    assert "模擬帳戶" in html
    assert "不是你的交易所帳戶" in html


def test_the_price_source_is_declared_as_perpetual_not_standard():
    """現價來自永續公開行情 —— 標準合約沒有公開行情端點。
    兩者貼得很近但不是同一個數字,所以只餵眼睛,不做對帳。"""
    html = seed({"BTC-USDT": pos("BTC-USDT", 0.1, 70000.0)},
                {"BTC-USDT": 76000.0})
    assert "永續" in html and "只餵眼睛" in html


# ══════════════════════════════════════════════════════════
# 四、那個字面星號
# ══════════════════════════════════════════════════════════
def test_kv_labels_render_bold_instead_of_printing_asterisks():
    """2026-09-18 面板上印出了字面的 `**不知道**` —— 而它出現在
    最需要被看見的那個字上。"""
    out = dash.kv("**不知道**", "0")
    assert "<b>不知道</b>" in out
    assert "**" not in out


def test_the_whole_page_has_no_literal_asterisks():
    """整頁掃一次 —— 別的地方漏掉的星號也一起抓。

    只看**看得見的字**:<script>/<style> 裡的星號是原始碼註解,
    使用者看不到。第一版沒排除它們,於是抓到一行 JS 註解 ——
    一條會對無害的東西發警報的測試,久了就會被當成雜訊忽略。
    """
    import re
    page = re.sub(r"<(script|style)\b.*?</\1>", "", dash.render(),
                  flags=re.S | re.I)
    found = re.findall(r"[^<>]{0,70}\*\*[^<>]{0,70}", page)
    assert not found, ("頁面上有沒被轉成粗體的字面星號:\n  "
                       + "\n  ".join(x.strip() for x in found))


# ══════════════════════════════════════════════════════════
# 五、兩張卡不准講同一件事
# ══════════════════════════════════════════════════════════
#
# 2026-09-18 執政官:「成績單跟模擬持倉是不是重複了?」
# 是 —— 權益兩張卡都印。分工重新切成:
#   模擬持倉 = 現在抱著什麼(部位層)
#   成績單   = 這套行不行(帳戶層)
# 一個數字只出現在一個地方。兩個地方各印一次的話,哪天它們因為
# 取數時點不同而對不起來,看的人只會困惑,不會知道該信哪一個。

def _labels(html_text: str) -> set:
    import re
    return {re.sub(r"<[^>]+>", "", m)
            for m in re.findall(r'<div class="l">(.*?)</div>', html_text)}


def test_the_positions_card_does_not_repeat_account_level_numbers():
    held = {"BTC-USDT": pos("BTC-USDT", 0.1, 70000.0)}
    labels = _labels(seed(held, {"BTC-USDT": 76000.0}))
    for owned_by_scorecard in ("權益", "已實現", "模擬報酬", "最大回撤"):
        assert owned_by_scorecard not in labels, (
            f"「{owned_by_scorecard}」是成績單的欄位,持倉卡不該再印一次")


def test_the_positions_card_keeps_what_is_genuinely_about_holdings():
    held = {"BTC-USDT": pos("BTC-USDT", 0.1, 70000.0)}
    labels = _labels(seed(held, {"BTC-USDT": 76000.0}))
    assert {"未實現合計", "持倉檔數"} <= labels


def test_the_positions_card_points_at_the_scorecard_for_the_rest():
    """拿掉數字之後要說它搬到哪去了 —— 不然看起來像壞掉。"""
    html = seed({"BTC-USDT": pos("BTC-USDT", 0.1, 70000.0)},
                {"BTC-USDT": 76000.0})
    assert "成績單" in html and "現在抱著什麼" in html
