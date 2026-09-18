"""
出場價 —— 這一單打算在哪裡結束 · 2026-09-18

執政官:「我希望還有出場價。」

═══ 出場價不是止損 ═══
面板上原本只有止損,而止損**不是策略的一部分** —— 它是 25% 的災難
後備,回答的是「機器死掉時這個倉最多虧多少」。策略真正的出場是
「跌破均線」或「跌破 N 日最低」,那條線每天都在動。

兩個混為一談的後果很具體:看到 25% 會以為策略打算讓它跌 25%,
而策略其實打算在 -12% 就走人。

═══ 這一組守的 ═══
一、出場價**跟策略要**,不跟任何剛好等於它的常數要
二、包一層(縮放 / 波動目標)不准把出場價弄不見
三、沒有出場價要說「為什麼沒有」,不能印成空白
四、出場價比止損遠的時候要講出來 —— 那代表後備線在替策略做決定
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio.rules import (donchian_breakout, exit_level_of, hold_all,
                             ma_filter, registry, scaled, vol_target)
from portfolio.ticket import OPEN_LONG, build

NOW = datetime(2026, 9, 18, 8, 0, tzinfo=timezone.utc)


@dataclass
class B:
    t: datetime
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0


def series(closes):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    dates = [t0 + timedelta(days=i) for i in range(len(closes))]
    idx = {"X": {dates[i]: B(dates[i], c, c, c, c)
                 for i, c in enumerate(closes)}}
    return dates, idx


# ══════════════════════════════════════════════════════════
# 一、出場價來自策略本身
# ══════════════════════════════════════════════════════════
def test_the_ma_exit_is_that_same_moving_average():
    """進場條件與出場條件是同一條線的兩面 —— 所以值要完全相等。"""
    dates, idx = series([100.0] * 9 + [200.0])       # 10 天,最後一天跳高
    price, how = exit_level_of(ma_filter(["X"], 10), "X", dates, idx, 9)
    assert price == sum([100.0] * 9 + [200.0]) / 10
    assert "10 日均線" in how


def test_a_different_ma_gives_a_different_exit():
    """**這條是防「拿剛好相等的常數當出場價」。**

    paper.plan() 裡的 `mas` 是用 vol_lookback(現在是 50)算的,
    策略也是 50 日均線 —— 兩個 50 只是剛好相等。哪天策略換成 100,
    出場價必須跟著變成 100,不能還報 50 的那條線。
    """
    dates, idx = series([float(100 + i) for i in range(120)])
    a, _ = exit_level_of(ma_filter(["X"], 50), "X", dates, idx, 119)
    b, _ = exit_level_of(ma_filter(["X"], 100), "X", dates, idx, 119)
    assert a != b, "換了均線長度,出場價卻沒變 —— 它不是跟策略要的"


def test_the_breakout_exit_is_the_n_day_low():
    """突破類的出場是 N 日最低,不是均線。"""
    dates, idx = series([float(100 + i) for i in range(40)])
    price, how = exit_level_of(
        donchian_breakout(["X"], 20, 10), "X", dates, idx, 39)
    # 第 39 天之前 10 天的最低 = 第 29 天的收盤 129
    assert price == 129.0
    assert "10 日最低" in how


# ══════════════════════════════════════════════════════════
# 二、包裝器不准把它弄不見
# ══════════════════════════════════════════════════════════
def test_wrapping_keeps_the_exit_level():
    """縮放與波動目標只改部位大小,**不改進出時點**。

    不轉發的話,包一層就把出場價弄不見,而畫面上看起來只會是
    「這個策略沒有出場價」—— 沒有人會發現是被包掉的。
    """
    dates, idx = series([float(100 + i) for i in range(60)])
    raw = ma_filter(["X"], 50)
    want, _ = exit_level_of(raw, "X", dates, idx, 59)
    for wrapped in (scaled(raw, 0.5),
                    vol_target(raw, 27.0, 50, ["X"], 3.0),
                    vol_target(scaled(raw, 0.5), 27.0, 50, ["X"], 3.0)):
        got, _ = exit_level_of(wrapped, "X", dates, idx, 59)
        assert got == want, "包一層之後出場價不見了"


def test_every_rule_in_the_registry_answers_the_question():
    """每一條策略都要回答「你的出場價是什麼」——

    **包括回答「我沒有」。** 沒有出場價是合法的(買入持有就是),
    不合法的是不回答:那會印成一個空白,而空白看起來像沒事。
    """
    dates, idx = series([float(100 + i) for i in range(260)])
    for name, fn in registry(["X"]).items():
        price, how = exit_level_of(fn, "X", dates, idx, 259)
        assert how, f"{name} 沒有說出場價是什麼,也沒說為什麼沒有"
        if price is None:
            assert "沒有" in how or "不夠" in how, f"{name}:{how}"


def test_buy_and_hold_says_it_has_no_exit_rather_than_going_blank():
    price, how = exit_level_of(hold_all(["X"]), "X", *series([1.0]), 0)
    assert price is None
    assert "沒有" in how


# ══════════════════════════════════════════════════════════
# 三、指令單上:出場價與止損是兩件事
# ══════════════════════════════════════════════════════════
def a_ticket(**kw):
    args = dict(symbol="AAVEUSDT", action=OPEN_LONG, quantity=5.4,
                price=128.52, leverage=3.0, stop_pct=25.0,
                strategy="50日均線之上才持有", signal_day="2026-09-18",
                exit_price=112.40, exit_rule="跌破 50 日均線", now=NOW)
    args.update(kw)
    return build(**args)


def test_the_ticket_shows_both_lines_and_which_arrives_first():
    rows = a_ticket().exit_plan()
    labels = [r[0] for r in rows]
    assert "出場價" in labels and "止損" in labels
    assert "哪一條先到" in labels
    text = " ".join(str(x) for r in rows for x in r)
    assert "12.5%" in text and "25.0%" in text


def test_the_exit_line_is_not_to_be_typed_into_the_app():
    """它每天會動,而且策略用收盤判定、App 的止損是盤中觸價 ——
    填進去等於把「收盤跌破才走」換成「盤中戳到就走」。"""
    text = " ".join(str(x) for r in a_ticket().exit_plan() for x in r)
    assert "不要填進 App" in text


def test_a_stop_nearer_than_the_exit_is_called_out():
    """止損比出場價近 = 後備線在替策略做決定。**要看得見。**

    這不是理論情況:高槓桿把止損拉近,或這檔離均線很遠的時候,
    先被觸發的就會是止損,而回測裡出場的是策略那條線 ——
    實際與回測從此是兩件事,且看不出來。
    """
    rows = a_ticket(leverage=3.0, stop_pct=5.0).exit_plan()
    text = " ".join(str(x) for r in rows for x in r)
    assert "止損比出場價近" in text


def test_an_exit_on_the_wrong_side_of_the_price_is_called_out():
    """做多而出場線在現價**上面** —— 一開就符合出場條件。"""
    rows = a_ticket(exit_price=140.0).exit_plan()
    text = " ".join(str(x) for r in rows for x in r)
    assert "方向不對" in text and "先別按" in text


def test_a_ticket_without_an_exit_says_so_instead_of_going_blank():
    rows = a_ticket(exit_price=None, exit_rule="這個計畫沒帶出場價").exit_plan()
    assert rows[0][0] == "出場價" and rows[0][1] == "—"
    assert "沒帶出場價" in rows[0][2]


def test_the_stop_explanation_no_longer_hard_codes_the_moving_average():
    """「策略出場是均線」原本寫死在 why() 裡 —— 換成突破策略就是假話。"""
    text = " ".join(str(x) for r in
                    a_ticket(exit_rule="跌破 10 日最低").why() for x in r)
    assert "跌破 10 日最低" in text
    assert "策略出場是均線" not in text


# ══════════════════════════════════════════════════════════
# 四、對齊單也要有出場價
# ══════════════════════════════════════════════════════════
def test_catch_up_tickets_carry_the_exit_price_despite_the_symbol_rename():
    """**代號格式不一樣是這裡唯一的陷阱。**

    plan() 的 exits 是用 `BTC-USDT` 當 key,catch_up 迴圈裡的 symbol
    已經轉成 App 代號 `BTCUSDT` 了。不轉 key 的話每一張對齊單都會
    安靜地變成「沒有出場價」—— 而畫面上看起來只像這個策略本來就沒有
    出場條件,不會有人發現是對不起來。
    """
    from portfolio.ticket import catch_up
    made, refused, _ = catch_up(
        {"BTC-USDT": 0.05}, [], {"BTC-USDT": 76000.0},
        stop_pct=25.0, leverage=3.0, strategy="50日均線之上才持有",
        exits={"BTC-USDT": (68000.0, "跌破 50 日均線")})
    assert not refused, refused
    assert len(made) == 1
    assert made[0].symbol == "BTCUSDT"
    assert made[0].exit_price == 68000.0, "出場價沒接上 —— 代號對不起來"
    assert "50 日均線" in made[0].exit_rule


def test_catch_up_without_exits_says_so_rather_than_inventing_one():
    from portfolio.ticket import catch_up
    made, _, _ = catch_up({"BTC-USDT": 0.05}, [], {"BTC-USDT": 76000.0},
                          stop_pct=25.0, leverage=3.0)
    assert made[0].exit_price is None
    assert made[0].exit_rule
