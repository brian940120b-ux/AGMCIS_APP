"""
U 本位標準合約 —— 交易所少給的那幾格 · 2026-09-13

守四件事:
一、強平價交易所不給,我們算 —— 但**算的跟給的不准混在同一格**
二、我們算的**偏樂觀**,而且那件事要說出來
三、讀不懂的持倉**不准安靜消失**
四、規格從成交史反推是**下界**,不是規格
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from exchange.bingx.standard_usdt import (InferredSpec, StandardPosition,
                                          infer_spec, liquidation_price,
                                          parse_position, positions_from)
from portfolio.account import MAINT_MARGIN_RATE, Position

# 2026-09-13 從 Demo 帳戶實際問到的那一筆。**不是編出來的。**
FLOCK = {
    "symbol": "FLOCKUSDT",
    "positionSide": "SHORT",
    "positionAmt": 3911.11,
    "entryPrice": 0.08088,
    "leverage": 20,
    "isolated": True,
    "initialMargin": 15.84,
    "unrealizedProfit": 3.911,
    "currentPrice": 0.07988,
    "time": 1757759431000,
}


# ══════════════════════════════════════════════════════════
# 一、它真的是線性合約 —— 這是「帳本不用重寫」的整個依據
# ══════════════════════════════════════════════════════════
def test_the_exchanges_own_numbers_say_this_is_a_linear_contract():
    """名目 ÷ 槓桿 = 交易所回的 initialMargin。

    這條式子成立,才代表 U 本位標準合約是線性(USDT 保證金)的,
    account.py 現成的每一條算式才適用。它不成立的話,整個帳本
    要照反向合約重寫 —— 那是幣本位的處境,不是這裡的。
    """
    notional = FLOCK["positionAmt"] * FLOCK["entryPrice"]
    assert notional / FLOCK["leverage"] == pytest.approx(
        FLOCK["initialMargin"], rel=0.005), \
        "保證金對不上名目÷槓桿 —— 這就不是線性合約,帳本要重寫"


def test_our_liq_formula_matches_the_one_in_account_py():
    """強平價只能有一條公式。**兩把尺是這個專案犯過十二次的錯。**"""
    ours = liquidation_price(FLOCK["entryPrice"], FLOCK["leverage"],
                             is_long=False)
    theirs = Position(symbol="FLOCKUSDT",
                      position_amt=-FLOCK["positionAmt"],
                      avg_price=FLOCK["entryPrice"],
                      leverage=FLOCK["leverage"]).liq_price()
    assert ours == pytest.approx(theirs)


def test_liq_price_is_above_entry_for_a_short():
    """空單被打爆是往上打。方向錯了,整個風控就是反的。"""
    px = liquidation_price(0.08088, 20, is_long=False)
    assert px > 0.08088
    assert px == pytest.approx(0.08088 * (1 + 1 / 20 - MAINT_MARGIN_RATE))


def test_liq_price_returns_none_rather_than_a_number_it_cannot_justify():
    """算不出來就回 None。**不回 0** —— 0 是一個看起來很安全的價格。"""
    assert liquidation_price(0, 20, True) is None
    assert liquidation_price(100, 0, True) is None
    # 1 倍槓桿撐得住 99.5% 的跌幅 —— 那是一個真的數字,不是算不出來。
    assert liquidation_price(100, 1, True) == pytest.approx(100 * 0.005)
    # 槓桿高到 1/L 被維持保證金率吃光(200×),緩衝歸零 —— 這才是 None。
    assert liquidation_price(100, 1 / MAINT_MARGIN_RATE, True) is None


# ══════════════════════════════════════════════════════════
# 二、算的跟給的不准混在同一格
# ══════════════════════════════════════════════════════════
def test_computed_liq_price_is_labelled_and_flagged_optimistic():
    pos = parse_position(FLOCK)
    assert pos is not None
    assert pos.liq_source == "computed", "必須標明這是我們算的"
    assert pos.liq_optimistic is True, \
        "沒計入維持保證金分層,實際強平會更近 —— 這件事要說出來"
    assert any("偏樂觀" in n for n in pos.notes)


def test_the_exchanges_own_liq_price_wins_when_it_gives_one():
    """交易所給了就用交易所的。我們算的只是備援。"""
    row = dict(FLOCK, liquidationPrice=0.0999)
    pos = parse_position(row)
    assert pos.liq_price == 0.0999
    assert pos.liq_source == "exchange"
    assert pos.liq_optimistic is False


def test_missing_mark_price_is_reported_not_swallowed():
    """這個產品只有 currentPrice。用它估強平距離有誤差,要講。"""
    pos = parse_position(FLOCK)
    assert any("markPrice" in n for n in pos.notes)


# ══════════════════════════════════════════════════════════
# 三、讀不懂的不准安靜消失
# ══════════════════════════════════════════════════════════
def test_an_unreadable_position_raises_instead_of_vanishing():
    """安靜丟掉一筆持倉 = 讓風控以為倉比實際少。

    那是最壞的一種錯:它給出一個確定的答案,而那個答案是錯的。
    """
    with pytest.raises(ValueError) as e:
        positions_from([FLOCK, {"symbol": "X", "positionAmt": None}])
    assert "不當成沒有倉" in str(e.value)


def test_a_position_with_no_direction_at_all_is_not_guessed():
    """方向猜錯,損益與強平價全部是反的,而它會長得完全正常。"""
    assert parse_position(
        {"symbol": "X", "positionAmt": 0, "entryPrice": 1.0}) is None


def test_liq_distance_returns_none_not_a_comfortable_number():
    """算不出距離就回 None。呼叫端必須當成不合格,不是當成沒問題。"""
    pos = StandardPosition(symbol="X", side="LONG", qty=1, entry=100,
                           leverage=10, isolated=True)
    assert pos.liq_distance_pct() is None


def test_liq_distance_is_computed_off_the_current_price():
    pos = parse_position(FLOCK)
    got = pos.liq_distance_pct()
    expected = abs(FLOCK["currentPrice"] - pos.liq_price) \
        / FLOCK["currentPrice"] * 100.0
    assert got == pytest.approx(expected)


# ══════════════════════════════════════════════════════════
# 四、反推出來的規格是下界,不是規格
# ══════════════════════════════════════════════════════════
ORDERS = [
    {"executedQty": "0.001", "avgPrice": "76676.7", "leverage": 20,
     "isolated": True},
    {"executedQty": "0.0025", "avgPrice": "76680.15", "leverage": 20,
     "isolated": True},
    {"executedQty": "1", "closePrice": "76600", "leverage": 5,
     "isolated": True},
]


def test_precision_comes_from_the_finest_fill_actually_seen():
    spec = infer_spec("BTCUSDT", ORDERS)
    assert spec.samples == 3
    assert spec.quantity_precision == 4     # 0.0025
    assert spec.price_precision == 2        # 76680.15
    assert spec.min_executed_qty == pytest.approx(0.001)
    assert spec.usable is True


def test_precision_is_read_off_the_string_not_through_float():
    """float('0.001') 的十進位展開有 20 位。精度不能經過二進位。"""
    spec = infer_spec("X", [{"executedQty": "0.001", "avgPrice": "1.5"}])
    assert spec.quantity_precision == 3, \
        "拿 float 去數小數位會數出 20 位 —— 精度不能經過二進位"


def test_the_inferred_spec_says_out_loud_that_it_is_only_a_lower_bound():
    """看到最細 4 位,只代表**至少**支援 4 位。這句話一定要在。"""
    spec = infer_spec("BTCUSDT", ORDERS)
    assert "下界" in spec.reason


def test_no_history_means_no_spec_not_a_default():
    """沒有樣本就回 None。一個猜出來的精度會產生一張被拒的單。"""
    spec = infer_spec("NEWCOIN", [])
    assert spec.usable is False
    assert spec.quantity_precision is None
    assert "不要套用其他標的的精度" in spec.reason


def test_a_spec_missing_either_precision_is_not_usable():
    spec = infer_spec("X", [{"leverage": 5}])
    assert isinstance(spec, InferredSpec)
    assert spec.usable is False
    assert "不得下單" in spec.reason
