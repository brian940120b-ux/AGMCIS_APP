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


# ══════════════════════════════════════════════════════════
# 七、為什麼是這一單
# ══════════════════════════════════════════════════════════
def with_reason(**kw):
    args = dict(reason="新開倉:收盤站上 50 日均線",
                signal=(76000.0, 72149.2, 5.34),
                weight_from=0.0, weight_to=0.14)
    args.update(kw)
    return a_ticket(**args)


def test_a_ticket_says_why_not_just_what():
    """2026-09-18 執政官:「我希望他能夠給我為什麼開單,理由是什麼。」

    一張說不出理由的單不該被按下去 —— 那等於把判斷外包給一個你看不見
    的東西,而虧錢的時候你連哪裡想錯了都查不出來。
    """
    why = dict(with_reason().why())
    assert "站上 50 日均線" in why["訊號"]
    assert "72,149.2" in why["證據"], "結論要帶證據,不能只有結論"
    assert "14.0%" in why["配置"]


def test_the_reason_never_invents_evidence_it_does_not_have():
    """缺訊號數字就少一行,**不是編一行**。"""
    why = dict(a_ticket(reason="平倉:收盤跌破 50 日均線").why())
    assert "訊號" in why
    assert "證據" not in why


def test_the_stop_explains_that_it_is_not_the_strategy_exit():
    """把後備停損當成策略出場,是 2026-09-13 記過的那個陷阱。"""
    assert "災難後備" in dict(with_reason().why())["止損怎麼來的"]


# ══════════════════════════════════════════════════════════
# 八、欄位照 BingX 開單畫面
# ══════════════════════════════════════════════════════════
def test_fields_match_the_real_standard_contract_order_form():
    """2026-09-18 執政官拍了開單畫面,而我的欄位是錯的。

    那張表單**根本沒有「數量」這一格** —— 標準合約是用**本金**下單的。
    我把「數量」排在第一個要填的位置,而那一格在 App 上不存在。
    """
    labels = [k for k, _, _ in with_reason().fields()]
    assert labels == ["① 保證金模式", "② 槓桿", "③ 方向", "④ 本金", "⑤ 止損"]
    assert not any("數量" in k for k in labels), "數量不是輸入欄"


def test_the_direction_uses_the_apps_own_words():
    """App 上寫的是「開多 / 開空」,不是「買入/做多」。"""
    got = {k: v for k, v, _ in with_reason().fields()}
    assert got["③ 方向"] == "開多"


def test_the_principal_is_the_only_input_and_it_is_the_margin():
    got = {k: v for k, v, _ in with_reason().fields()}
    assert got["④ 本金"] == "253.33"            # 760 ÷ 3


def test_a_leverage_below_the_preset_buttons_says_so():
    """檔位是 5x 10x 17x 19x 20x 40x —— **3× 不在裡面**,要用 ✏️ 自訂。

    不說的話,人會直接按 5x,而那是槓桿差 67% 的一張單。
    """
    note = [n for k, _, n in with_reason().fields() if k == "② 槓桿"][0]
    assert "✏️" in note and "5x" in note


def test_quantity_and_notional_moved_to_the_check_after_list():
    """它們是**填完之後用來核對的**,不是拿來填的。

    對不上就代表某一格填錯了 —— 而那比填漏一格更難發現:
    一張數量錯十倍的單會成交。
    """
    got = {k: v for k, v, _ in with_reason().verify_after()}
    assert got["交易總額"] == "760.00"
    assert got["成交後持倉"] == "0.01"
    assert "預估強平" in got


def test_values_are_clean_strings_ready_to_paste():
    """沒有千分位、沒有單位。**多一個逗號就是一張被拒的單。**

    只管**要填的**那幾格 —— verify_after() 是給人讀的,可以有千分位。
    """
    for _, value, _ in with_reason().fields():
        assert "," not in value
        assert "USDT" not in value and "×" not in value


def test_no_markdown_asterisks_leak_into_the_pasted_text():
    """這些字串會被 html.escape 後直接印 —— 星號不會變粗體,
    只會變成兩個星號。"""
    for _, value, note in with_reason().fields():
        assert "**" not in value and "**" not in note


# ══════════════════════════════════════════════════════════
# 九、不猜 deeplink
# ══════════════════════════════════════════════════════════
def test_the_app_scheme_is_the_one_the_share_link_proved():
    """**`bingbon://`,不是 `bingx://`。**

    BingX 的前身是 Bingbon,而 App 的 URL scheme 沒跟著改名。
    我猜的三條 `bingx://` 全打不開;2026-09-18 執政官分享了一條真的
    連結,把 activePageUrl 解碼出來才看到。**這種事猜不到。**
    """
    from portfolio.ticket import bingx_links

    app = bingx_links("BTCUSDT")[0][1]
    assert app.startswith("bingbon://trade/detail?")
    assert "coinName=BTC" in app
    assert "valuationCoinName=USDT" in app
    assert "bingx://" not in app, "那個猜錯的 scheme 不准回來"


def test_the_link_says_it_does_not_carry_the_product():
    """**連結只帶幣種,不帶產品別。**

    2026-09-18:我看到網址路徑裡的 /perpetual/ 就說它是永續頁 ——
    判斷錯了。那是 BingX 分享功能的格式,不是產品別;執政官那條
    就是從標準合約頁分享出來的。拿字串的長相去推斷它的語意,又一次。

    真正要緊的是:App 連結的參數只有 coinName / valuationCoinName /
    marginCoinName,**沒有任何一個說產品別**。
    """
    from portfolio.ticket import bingx_links

    app = [x for x in bingx_links("BTCUSDT")
           if x[1].startswith("bingbon://")][0]
    assert "productType" not in app[1] and "contractType" not in app[1]
    assert "不帶產品別" in app[2]


def test_every_ticket_warns_to_check_the_product_tab_first():
    """**指令單的數量是照標準合約算的,下到永續上就是一張算錯的單** ——
    而它會成交。

    同一個幣在兩個產品上的規格、槓桿上限、費率都不一樣,而連結不會
    幫你選。所以每一張單都要提醒。
    """
    warned = [w for w in a_ticket().warnings if "U 本位標準合約" in w]
    assert warned, "每張單都要有這個提醒"
    assert "會成交" in warned[0], "要說出後果,不是只說「請注意」"


def test_an_unsplittable_symbol_does_not_produce_a_link_to_another_coin():
    """切不開就整串當 base —— **不猜**。

    猜錯的話會產生一條指向**別的幣**的連結,而那種錯最貴:
    畫面上寫 A,連結開到 B,而兩邊都看起來正常。
    """
    from portfolio.ticket import split_symbol

    assert split_symbol("WEIRD") == ("WEIRD", "USDT")
    assert split_symbol("BTC-USDT") == ("BTC", "USDT")
    assert split_symbol("ETHUSDC") == ("ETH", "USDC")


def test_a_verified_template_can_be_supplied_without_touching_code(monkeypatch):
    """哪天拿到真的會動的連結,設環境變數就好,不用改程式。"""
    import importlib

    monkeypatch.setenv("BINGX_LINK_TEMPLATE", "https://x.test/{symbol}")
    import portfolio.ticket as mod
    importlib.reload(mod)
    try:
        assert mod.bingx_links("BTCUSDT")[0][1] == "https://x.test/BTCUSDT"
        assert len(mod.bingx_links("BTCUSDT")) == 1
        assert mod.BINGX_LINK_VERIFIED is True
    finally:
        monkeypatch.delenv("BINGX_LINK_TEMPLATE")
        importlib.reload(mod)


# ══════════════════════════════════════════════════════════
# 報價必須是**現在**的價格 · 2026-09-18
# ══════════════════════════════════════════════════════════
#
# 執政官:「價格跟交易所不一樣啊。」
#
# 對。plan() 的 prices 是 `idx[s][exec_day].o` —— 成交日那根日線的
# **開盤價**,Order.price 的註解自己寫著「預期成交價(隔日開盤)」。
# 那是回測的節奏(訊號用收盤、成交在隔日開盤,中間隔一個可交易的
# 間隙)。但人是**現在**在按的,而現在離那個開盤最多差 24 小時。
#
# 後果不只是數字難看:本金、數量、止損全部照那個價格算,所以照著填
# 會用一個錯的規模去冒一個不是原本那個的風險 —— 而這張單自己的價格帶
# 是 ±1%,它照自己的規則早就作廢了。

class _Order:
    def __init__(self, symbol, side, qty, price, notional, weight_to=0.14):
        self.symbol, self.side, self.qty = symbol, side, qty
        self.price, self.notional = price, notional
        self.reason, self.weight_from, self.weight_to = "", 0.0, weight_to


def _plan(order):
    return {"orders": [order], "signal_day": "2026-09-18", "cfg": None,
            "prices": {order.symbol: order.price}}


def test_a_ticket_is_quoted_at_the_live_price_not_yesterdays_open():
    from portfolio.ticket import make_tickets
    o = _Order("BTC-USDT", "BUY", 0.01, 70000.0, 700.0)
    made, refused = make_tickets(_plan(o), 25.0, 3.0,
                                 marks={"BTC-USDT": 77000.0})
    assert not refused, refused
    assert made[0].quoted_price == 77000.0, "報的還是日線開盤價"


def test_requoting_keeps_the_notional_the_strategy_asked_for():
    """**維持的是名目金額**(權益 × 目標權重)—— 那才是策略指定的東西。

    價格漲了就少買幾顆,不是照舊顆數買下去:照舊顆數會讓這一檔的
    曝險跟著價格一起長大,而目標權重根本沒變。
    """
    from portfolio.ticket import make_tickets
    o = _Order("BTC-USDT", "BUY", 0.01, 70000.0, 700.0)
    made, _ = make_tickets(_plan(o), 25.0, 3.0, marks={"BTC-USDT": 77000.0})
    assert made[0].quantity * 77000.0 == pytest.approx(700.0)


def test_a_close_ticket_keeps_its_quantity_whatever_the_price_does():
    """平 5.4 顆就是 5.4 顆。按名目重算會讓平倉平不乾淨,
    而殘倉是沒有人在管的倉。"""
    from portfolio.ticket import make_tickets
    o = _Order("BTC-USDT", "SELL", 5.4, 70000.0, 378000.0, weight_to=0.0)
    made, _ = make_tickets(_plan(o), 25.0, 3.0, marks={"BTC-USDT": 77000.0})
    assert made[0].quantity == 5.4


def test_without_a_live_price_the_ticket_says_it_is_quoting_stale():
    """一張用昨天開盤價報的單,跟一張用現價報的,在畫面上長得一模一樣。"""
    from portfolio.ticket import make_tickets
    o = _Order("BTC-USDT", "BUY", 0.01, 70000.0, 700.0)
    made, _ = make_tickets(_plan(o), 25.0, 3.0, marks={})
    assert made[0].quoted_price == 70000.0
    warns = " ".join(made[0].warnings)
    assert "問不到現價" in warns and "先對一下交易所現價" in warns


def test_the_price_band_is_actually_enforced_somewhere():
    """`price_in_band()` 2026-09-13 就寫好了,而面板**從來沒呼叫過**
    —— 「寫好沒接上」的第四次。一張照自己規則早該作廢的單還能按,
    那條規則等於不存在。

    現在由前端拿串流價檢查(那正是它該發生的時刻:頁面開著、價格在動)。
    """
    import scripts.dashboard as dash
    page = dash.render()
    assert "function tickBand(" in page
    assert "data-lo=" in dash._ticket_card(a_ticket())
    assert "價格跑掉了 · 不要按" in page
