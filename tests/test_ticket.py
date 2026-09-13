"""
下單指令單 —— 最後一吋是人按的 · 2026-09-13

自動下單的系統知道自己送了什麼。**手動下單的系統不知道。**
這組測試守的就是那個差別。

一、一張沒有停損的單不該存在
二、停損擺在強平之後 = 沒有停損
三、過期或跑出價格帶的單**不能按**,不是「參考一下」
四、按完要能查出人有沒有照做
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from exchange.bingx.standard_usdt import StandardPosition
from portfolio.ticket import (CLOSE, OPEN_LONG, OPEN_SHORT, TicketRefused,
                              build, ticket_id, verify)

NOW = datetime(2026, 9, 13, 8, 0, tzinfo=timezone.utc)


def a_ticket(**kw):
    args = dict(symbol="BTCUSDT", action=OPEN_LONG, quantity=0.01,
                price=76000.0, leverage=3.0, stop_pct=8.0,
                strategy="50MA", signal_day="2026-09-13", now=NOW)
    args.update(kw)
    return build(**args)


# ══════════════════════════════════════════════════════════
# 一、停損
# ══════════════════════════════════════════════════════════
def test_no_stop_percentage_means_no_ticket():
    """§19:一張沒有停損的單不該存在。**這裡不替執政官挑一個數字。**"""
    for bad in (0, 100, -5, 150):
        with pytest.raises(TicketRefused) as e:
            a_ticket(stop_pct=bad)
        assert "第 102 條" in str(e.value)


def test_a_short_stops_above_entry_not_below():
    """空單的停損在上面。方向錯了等於沒有停損。"""
    long_t = a_ticket(action=OPEN_LONG)
    short_t = a_ticket(action=OPEN_SHORT)
    assert long_t.stop_price < long_t.quoted_price
    assert short_t.stop_price > short_t.quoted_price


def test_a_stop_beyond_the_liquidation_price_is_refused():
    """停損擺在強平的另一邊 —— 倉會先被強平,停損永遠不會觸發。

    20× 的強平大約在 -4.5%,把停損放到 -8% 就是這個情況。
    """
    with pytest.raises(TicketRefused) as e:
        a_ticket(leverage=20.0, stop_pct=8.0)
    msg = str(e.value)
    assert "強平" in msg
    assert "降槓桿" in msg


def test_opening_too_close_to_liquidation_is_refused():
    """開倉當下離強平不到 20% 就不發單(§19)。

    5× 的強平距離約 19.5% —— 剛好在下限之下,而它**還是偏樂觀的**。
    """
    with pytest.raises(TicketRefused) as e:
        a_ticket(leverage=5.0, stop_pct=3.0)
    assert "偏樂觀" in str(e.value)


def test_a_valid_ticket_says_its_liq_estimate_is_optimistic():
    """看得到強平價,就要看得到它偏哪一邊。"""
    t = a_ticket()
    assert any("實際會更近" in w for w in t.warnings)
    assert t.est_liq_price is not None and t.est_liq_price < t.quoted_price


def test_every_gate_is_recorded_not_just_the_failures():
    t = a_ticket()
    names = [c[0] for c in t.checks]
    assert "停損在強平之前" in names
    assert all(passed for _, passed in t.checks)


# ══════════════════════════════════════════════════════════
# 二、有效期與價格帶
# ══════════════════════════════════════════════════════════
def test_an_expired_ticket_cannot_be_tapped():
    t = a_ticket()
    ok, why = t.executable(76000.0, now=NOW + timedelta(minutes=31))
    assert ok is False and "過期" in why


def test_a_ticket_outside_its_price_band_cannot_be_tapped():
    """價格走掉了,原本的數量就對不上原本要冒的風險。"""
    t = a_ticket()
    ok, why = t.executable(76000.0 * 1.02, now=NOW)
    assert ok is False and "重算" in why
    ok, why = t.executable(76000.0 * 0.98, now=NOW)
    assert ok is False


def test_a_fresh_ticket_at_the_quoted_price_is_executable():
    t = a_ticket()
    ok, why = t.executable(76000.0, now=NOW + timedelta(minutes=5))
    assert ok is True, why


def test_the_band_is_symmetric_around_the_quote():
    t = a_ticket()
    assert t.price_low == pytest.approx(76000.0 * 0.99)
    assert t.price_high == pytest.approx(76000.0 * 1.01)


# ══════════════════════════════════════════════════════════
# 三、冪等
# ══════════════════════════════════════════════════════════
def test_the_same_decision_is_always_the_same_ticket():
    """同一個決定重算一百次是同一個 id —— 才看得出「按了兩次」。"""
    assert a_ticket().ticket_id == a_ticket().ticket_id
    assert ticket_id("a", "b", "c", "d") != ticket_id("a", "b", "c", "e")


def test_a_ticket_is_frozen():
    """開出來就不准改。要改就重開一張,新的 id、新的有效期。"""
    t = a_ticket()
    with pytest.raises(Exception):
        t.quantity = 999


# ══════════════════════════════════════════════════════════
# 四、人有沒有照做
# ══════════════════════════════════════════════════════════
def a_position(**kw):
    args = dict(symbol="BTCUSDT", side="LONG", qty=0.01, entry=76000.0,
                leverage=3.0, isolated=True)
    args.update(kw)
    return StandardPosition(**args)


def test_a_position_that_matches_the_ticket_is_clean():
    ex = verify(a_ticket(), a_position())
    assert ex.clean is True
    assert ex.slippage_pct == pytest.approx(0.0)


def test_no_position_at_all_is_not_clean():
    """沒開成不算乾淨 —— 可能還沒按,也可能按失敗了。"""
    ex = verify(a_ticket(), None)
    assert ex.found is False and ex.clean is False


@pytest.mark.parametrize("field,value,label", [
    ("qty", 0.02, "數量"),
    ("leverage", 10.0, "槓桿"),
    ("side", "SHORT", "方向"),
    ("isolated", False, "保證金模式"),
])
def test_each_way_a_human_can_get_it_wrong_is_caught(field, value, label):
    """按錯數量、按錯方向、忘了改槓桿、忘了改保證金模式 ——
    四件事沒有一件會自己發出聲音。這一層就是那個聲音。"""
    ex = verify(a_ticket(), a_position(**{field: value}))
    assert ex.clean is False
    assert any(label in m for m in ex.mismatches), ex.mismatches


def test_slippage_sign_is_from_the_traders_side_not_the_charts():
    """做多成交價高了是吃虧,做空成交價低了才是吃虧。"""
    long_ex = verify(a_ticket(action=OPEN_LONG),
                     a_position(entry=76760.0))
    assert long_ex.slippage_pct == pytest.approx(1.0)

    short_ex = verify(a_ticket(action=OPEN_SHORT),
                      a_position(side="SHORT", entry=75240.0))
    assert short_ex.slippage_pct == pytest.approx(1.0)


def test_close_tickets_do_not_invent_a_direction():
    """平倉沒有 positionSide —— 不要編一個出來。"""
    t = a_ticket(action=CLOSE)
    assert t.position_side is None
    assert t.est_liq_price is None
    ex = verify(t, a_position(side="SHORT"))
    assert not any("方向" in m for m in ex.mismatches)


# ══════════════════════════════════════════════════════════
# 五、從今日計畫產生指令單
# ══════════════════════════════════════════════════════════
from dataclasses import dataclass  # noqa: E402

from portfolio.ticket import app_symbol, make_tickets  # noqa: E402


@dataclass
class FakeOrder:
    symbol: str
    side: str
    qty: float
    price: float
    weight_to: float = 0.1


class FakeCfg:
    strategy = "50MA"


def a_plan(orders):
    return {"orders": orders, "cfg": FakeCfg(), "signal_day": "2026-09-13"}


def test_symbols_are_converted_to_the_app_spelling():
    """App 與 allPosition 用無槓的寫法(實測 FLOCKUSDT)。"""
    assert app_symbol("BTC-USDT") == "BTCUSDT"
    assert app_symbol("BTCUSDT") == "BTCUSDT"


def test_a_zero_target_weight_becomes_a_close_not_a_short():
    """目標歸零是**平倉**,不是反手做空。搞混會開出一個反向的新倉。"""
    made, refused = make_tickets(
        a_plan([FakeOrder("BTC-USDT", "SELL", 0.01, 76000.0, weight_to=0.0)]),
        stop_pct=25.0, leverage=3.0)
    assert not refused
    assert made[0].action == CLOSE
    assert made[0].position_side is None


def test_a_refused_ticket_is_reported_not_dropped():
    """一張被閘門擋下的單如果安靜地不見了,看板上就會是「今天沒事」。

    而實際上是「今天有事,但系統拒絕告訴你」。
    """
    made, refused = make_tickets(
        a_plan([FakeOrder("BTC-USDT", "BUY", 0.01, 76000.0)]),
        stop_pct=25.0, leverage=20.0)          # 20× 的強平比停損還近
    assert made == []
    assert len(refused) == 1
    assert refused[0][0] == "BTC-USDT"
    assert "強平" in refused[0][1]


def test_the_decided_backstop_survives_the_leverage_cap():
    """執政官裁定的 25% + 上限 3× 必須開得出單來。

    這兩個數字是分開決定的,而它們**必須相容** —— 不相容的話
    系統每天都會印一張「開不出來」,而沒有人會發現那是設定衝突。
    """
    from exchange.bingx.trade import BACKSTOP_PCT
    from portfolio.paper import LEVERAGE_CAP

    made, refused = make_tickets(
        a_plan([FakeOrder("BTC-USDT", "BUY", 0.01, 76000.0)]),
        stop_pct=BACKSTOP_PCT, leverage=LEVERAGE_CAP)
    assert not refused, refused
    assert made[0].stop_price == pytest.approx(76000.0 * 0.75)


# ══════════════════════════════════════════════════════════
# 六、對齊:讓真實帳戶追上模擬帳戶
# ══════════════════════════════════════════════════════════
from portfolio.ticket import catch_up  # noqa: E402

PRICES = {"BTC-USDT": 76000.0, "ETH-USDT": 2500.0}


def test_an_empty_exchange_gets_a_ticket_for_every_simulated_position():
    """真實帳戶是空的 —— 模擬有幾個倉就要按幾張。

    這就是「今天沒有要按的」那句話漏掉的東西:模擬確實沒有換手,
    而真實帳戶差了整整幾個倉。
    """
    made, refused, notes = catch_up(
        {"BTC-USDT": 0.01, "ETH-USDT": 0.4}, [], PRICES,
        stop_pct=25.0, leverage=3.0)
    assert not refused and not notes
    assert {t.symbol for t in made} == {"BTCUSDT", "ETHUSDT"}
    assert all(t.action == OPEN_LONG for t in made)


def test_a_position_already_matching_produces_no_ticket():
    made, _, notes = catch_up(
        {"BTC-USDT": 0.01}, [a_position(qty=0.01)], PRICES,
        stop_pct=25.0, leverage=3.0)
    assert made == [] and notes == []


def test_only_the_shortfall_is_ticketed_not_the_whole_position():
    """已經有一半就只補一半。整筆重開會變成兩倍的倉。"""
    made, _, _ = catch_up(
        {"BTC-USDT": 0.01}, [a_position(qty=0.004)], PRICES,
        stop_pct=25.0, leverage=3.0)
    assert len(made) == 1
    assert made[0].quantity == pytest.approx(0.006)


def test_a_position_the_simulation_does_not_have_is_reported_not_closed():
    """那可能是執政官自己開的倉。**系統不替他決定平掉。**"""
    made, _, notes = catch_up(
        {}, [a_position(symbol="AVAUSDT", qty=100.0)], PRICES,
        stop_pct=25.0, leverage=3.0)
    assert made == []
    assert any("AVAUSDT" in n and "不替你決定平掉" in n for n in notes)


def test_reducing_or_flipping_is_flagged_not_auto_ticketed():
    """減倉與反手搞錯方向會開出一個反向的新倉,而那個倉沒有人在管。"""
    made, _, notes = catch_up(
        {"BTC-USDT": -0.01}, [a_position(qty=0.01)], PRICES,
        stop_pct=25.0, leverage=3.0)
    assert made == []
    assert any("反手" in n or "減倉" in n for n in notes)


def test_no_price_means_no_ticket_not_a_guessed_quantity():
    """問不到現價就不出單。**不猜** —— 猜錯的是下單量。"""
    made, refused, _ = catch_up(
        {"SOL-USDT": 3.0}, [], PRICES, stop_pct=25.0, leverage=3.0)
    assert made == []
    assert refused and "不猜" in refused[0][1]
