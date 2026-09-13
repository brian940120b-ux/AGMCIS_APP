"""
新系統 — 訂單 / 執行 / 帳本 · 2026-09-08

釘死三件會直接讓人虧錢、而且不會有任何測試自然變紅的事:
一、實盤閘門不能被設定檔打開
二、帳本必須平衡(現金 + 持倉 = 權益)
三、停損價必須是策略本身的規則(50 日均線),不是另挑的百分比
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from portfolio import execution, orders
from portfolio.account import MAINT_MARGIN_RATE, Account


# ══════════════════════════════════════════════════════════
# 一、實盤閘門
# ══════════════════════════════════════════════════════════
def test_live_is_off_and_is_a_source_constant():
    """實盤開關必須是原始碼常數 —— 能被設定檔打開的開關遲早會被打開。"""
    assert execution.LIVE_ENABLED is False
    src = Path(execution.__file__).read_text(encoding="utf-8")
    for banned in ("os.environ.get(\"LIVE", "getenv(\"LIVE",
                   "config[\"live", "argv"):
        assert banned not in src, f"實盤開關不得來自 {banned}"


def test_default_executor_is_paper():
    assert isinstance(execution.get_executor(), execution.PaperExecutor)
    assert execution.get_executor().venue == "PAPER"


def test_live_executor_refuses_even_if_called_directly():
    """就算有人直接實例化 LiveExecutor,它也必須拒絕。"""
    o = orders.Order(symbol="BTC-USDT", side="BUY", qty=1.0, price=100.0,
                     notional=100.0, reason="test")
    recs = execution.LiveExecutor().submit([o])
    assert recs[0]["status"] == "REJECTED"
    assert "LIVE_ENABLED" in recs[0]["error"]


def test_graduation_gate_blocks_live():
    """畢業契約沒過就不得實盤 —— 目前 1/8。"""
    ok, detail = execution.graduation_ok()
    assert ok is False, "契約已全過?那要人工確認,不該讓測試自動放行"
    assert "未過" in detail or "無法評估" in detail


def test_paper_executor_has_no_exchange_client():
    """紙上執行器不得有任何交易所連線路徑 —— 結構性保證,不靠自律。"""
    src = Path(execution.__file__).read_text(encoding="utf-8")
    head = src[:src.index("class LiveExecutor")]
    for banned in ("BingXClient", "requests.post", "api_key", "secret",
                   "place_order("):
        assert banned not in head, f"執行層不得出現 {banned}"


# ══════════════════════════════════════════════════════════
# 二、帳本平衡
# ══════════════════════════════════════════════════════════
def test_contract_accounting_does_not_spend_balance_on_open():
    """合約開倉**不花掉 USDT**,只鎖保證金。

    這是合約與現貨最根本的差別。首版用現貨式記法(買幣就扣現金),
    那讓「可用保證金」「強平價」「保證金率」三個欄位無法存在,
    而它們是合約交易員最先看的東西。
    """
    a = Account()
    a.fill("BTC-USDT", "BUY", 0.1, 1000.0, 0.07, "t")
    marks = {"BTC-USDT": 1000.0}
    # 餘額只被手續費改變,不因開倉而減少
    assert a.balance == pytest.approx(10_000.0 - 0.07)
    assert a.equity(marks) == pytest.approx(10_000.0 - 0.07)
    # 1× 槓桿:保證金 = 名目
    assert a.used_margin(marks) == pytest.approx(100.0)
    assert a.available_margin(marks) == pytest.approx(10_000.0 - 0.07 - 100.0)


def test_short_position_is_negative_amount():
    """合約規格:持倉量帶正負號,正=多、負=空。"""
    a = Account()
    a.fill("X", "SELL", 1.0, 100.0, 0.0, "t")
    p = a.positions["X"]
    assert p.position_amt == -1.0 and p.side == "SHORT"
    # 空單價格跌 = 賺
    assert p.unrealized(90.0) == pytest.approx(10.0)
    assert p.unrealized(110.0) == pytest.approx(-10.0)


def test_liquidation_price_and_margin_ratio():
    """強平價與保證金率必須算得出來 —— 那是合約的核心風險數字。"""
    a = Account()
    a.fill("X", "BUY", 1.0, 100.0, 0.0, "t")
    p = a.positions["X"]
    # 1× 槓桿:強平價 = 100 × (1 − 1 + 0.005) = 0.5
    assert p.liq_price() == pytest.approx(0.5)
    assert a.liquidation_check({"X": 100.0}) == []
    assert a.liquidation_check({"X": 0.4}) == ["X"]
    # 風險率 = (維持保證金 + 平倉手續費) / (倉位保證金 + 未實現)
    # 2026-09-09 對齊 BingX 原文,分子補上平倉手續費:
    #   維持保證金 100×1×0.5% = 0.5、平倉手續費 100×1×0.05% = 0.05
    #   倉位保證金 100×1/1 = 100、未實現 0  → (0.5+0.05)/100 = 0.0055
    from portfolio.costs import TAKER_FEE_PCT
    expect = (MAINT_MARGIN_RATE + TAKER_FEE_PCT / 100.0)
    assert p.margin_ratio(100.0) == pytest.approx(expect)
    assert p.margin_ratio(100.0) == pytest.approx(0.0055)


def test_initial_margin_uses_entry_price_not_mark():
    """起始保證金鎖在開倉那一刻,不隨標記價浮動。

    BingX 原文:position margin = avg. position price / leverage * size。
    2026-09-09 之前這裡用標記價算,導致「已用保證金」會隨行情跳動 ——
    那是錯的模型:浮動的是未實現盈虧,不是已經鎖進去的保證金。
    """
    a = Account()
    a.fill("X", "BUY", 2.0, 100.0, 0.0, "t", leverage=10.0)
    p = a.positions["X"]
    assert p.initial_margin() == pytest.approx(100.0 * 2.0 / 10.0)
    # 價格翻倍也不能改變它
    assert a.used_margin({"X": 200.0}) == pytest.approx(20.0)
    assert a.used_margin({"X": 50.0}) == pytest.approx(20.0)
    # ROI 用交易所公式:(標記−均價)/均價 × 槓桿 × 100
    assert p.roi(110.0) == pytest.approx((110 - 100) / 100 * 10 * 100)


def test_short_receives_funding_when_rate_positive():
    """做多付、做空收 —— 符號跟著部位方向走。

    舊系統的回測引擎沒有方向符號,空單被當成要付資金費,
    等於每年多扣空單 5.69%。
    """
    long_ = Account(); long_.fill("X", "BUY", 1.0, 100.0, 0.0, "t")
    short = Account(); short.fill("X", "SELL", 1.0, 100.0, 0.0, "t")
    assert long_.charge_funding({"X": 100.0}, {"X": 0.0002}) == pytest.approx(0.02)
    assert short.charge_funding({"X": 100.0}, {"X": 0.0002}) == pytest.approx(-0.02)
    assert short.balance > 10_000.0, "空單在正費率下應該收到錢"


def test_sell_realises_pnl_and_clears_position():
    a = Account()
    a.fill("BTC-USDT", "BUY", 1.0, 100.0, 0.0, "t")
    r = a.fill("BTC-USDT", "SELL", 1.0, 120.0, 0.0, "t")
    assert r == pytest.approx(20.0)
    assert a.realized_pnl == pytest.approx(20.0)
    assert "BTC-USDT" not in a.positions
    assert a.balance == pytest.approx(10_020.0)


def test_average_entry_price_on_add():
    a = Account()
    a.fill("X", "BUY", 1.0, 100.0, 0.0, "t")
    a.fill("X", "BUY", 1.0, 200.0, 0.0, "t")
    assert a.positions["X"].avg_price == pytest.approx(150.0)


def test_funding_is_charged_on_holdings_only():
    a = Account()
    a.fill("X", "BUY", 1.0, 100.0, 0.0, "t")
    paid = a.charge_funding({"X": 100.0}, {"X": 0.0002})
    assert paid == pytest.approx(0.02)
    assert a.funding_paid == pytest.approx(0.02)
    empty = Account()
    assert empty.charge_funding({}, {}) == 0.0


# ══════════════════════════════════════════════════════════
# 三、訂單
# ══════════════════════════════════════════════════════════
def test_stop_is_the_moving_average_not_an_invented_percent():
    """停損價必須等於傳進來的均線。

    另挑一個百分比等於偷偷加一個沒被回測過的參數,
    而 Calmar 1.33 是在「跌破均線才出場」這條規則下算出來的。
    """
    os_ = orders.build_orders({"ETH-USDT": 1.0}, {}, 1000.0,
                              {"ETH-USDT": 100.0}, {"ETH-USDT": 90.0})
    assert len(os_) == 1
    assert os_[0].stop == 90.0
    assert os_[0].stop_pct == pytest.approx(10.0)


def test_tiny_adjustments_are_skipped():
    """小於門檻的調整不下單 —— 手續費會吃掉它。"""
    assert orders.build_orders({"X": 0.502}, {"X": 0.500}, 10_000.0,
                               {"X": 100.0}) == []
    assert orders.build_orders({"X": 0.001}, {}, 1_000.0, {"X": 100.0}) == []


def test_orders_are_deltas_not_full_positions():
    """已持有的部分不得重複下單。"""
    os_ = orders.build_orders({"ETH-USDT": 0.5}, {"ETH-USDT": 0.2},
                              10_000.0, {"ETH-USDT": 100.0})
    assert len(os_) == 1 and os_[0].side == "BUY"
    assert os_[0].notional == pytest.approx(3_000.0)


def test_exit_order_is_emitted_when_signal_turns_off():
    os_ = orders.build_orders({}, {"ETH-USDT": 0.3}, 10_000.0,
                              {"ETH-USDT": 100.0})
    assert len(os_) == 1 and os_[0].side == "SELL"
    assert "跌破" in os_[0].reason


# ══════════════════════════════════════════════════════════
# 四、監控層不得自己調參數
# ══════════════════════════════════════════════════════════
def test_monitor_never_reoptimises():
    """監控層只准觀察與示警。自動調參數 = 把過擬合自動化,
    那正是舊系統 1391 案全滅的原因。"""
    from portfolio import monitor
    src = Path(monitor.__file__).read_text(encoding="utf-8")
    for banned in ("VOL_TARGET_ANNUAL_PCT =", "optimize", "best_param",
                   "grid_search", "write_text"):
        assert banned not in src, f"監控層不得出現 {banned}"
    assert monitor.BREACH_MULT == 1.3
    assert monitor.MIN_DAYS == 30


# ══════════════════════════════════════════════════════════
# 五、交易所規格(2026-09-09 加)
#
# 排查發現七個持倉的數量全部不符 BingX 數量精度,真的派單一張都送不出去。
# 紙上交易看不出來:帳本照收、面板照顯示、測試照過 —— 所以要有這一組。
# ══════════════════════════════════════════════════════════
def test_every_generated_order_is_exchange_acceptable():
    """訂單層產出的每一張單都必須符合交易所規格,否則會被拒單。"""
    from portfolio import specs
    from portfolio.paper import SYMBOLS
    prices = {"BTC-USDT": 78_000.0, "ETH-USDT": 2_480.0, "SOL-USDT": 103.3,
              "BNB-USDT": 752.0, "XRP-USDT": 1.4165, "AAVE-USDT": 128.7,
              "UNI-USDT": 6.747}
    target = {s: 1.0 / len(SYMBOLS) for s in SYMBOLS}
    os_ = orders.build_orders(target, {}, 10_000.0, prices)
    assert os_, "應該要產生訂單"
    for o in os_:
        sp = specs.spec(o.symbol)
        qp = sp["quantity_precision"]
        assert o.qty == pytest.approx(round(o.qty, qp), abs=1e-12), \
            f"{o.symbol} 數量 {o.qty} 超出 {qp} 位精度 → 交易所會拒單"
        assert o.qty >= sp["min_qty"], f"{o.symbol} 低於最小下單量"
        assert o.notional >= sp["min_notional"], f"{o.symbol} 低於最小名目"
        pp = sp["price_precision"]
        assert o.price == pytest.approx(round(o.price, pp), abs=1e-12), \
            f"{o.symbol} 價格 {o.price} 超出 {pp} 位精度"


def test_quantity_is_truncated_never_rounded_up():
    """數量一律無條件捨去 —— 進位會讓部位大於預期、佔用更多保證金。"""
    from portfolio import specs
    # UNI 精度 0 位:103.9 只能變成 103,不能變成 104
    assert specs.round_qty("UNI-USDT", 103.9) == 103.0
    # BTC 精度 4 位
    assert specs.round_qty("BTC-USDT", 0.00899999) == pytest.approx(0.0089)


def test_unknown_symbol_raises_instead_of_guessing():
    """查不到規格必須當場中斷,不得猜一個預設值。

    舊系統教訓五:用預設值吞掉錯誤 → 檢查靜靜跳過 → 稽核天天報「正常」。
    """
    from portfolio import specs
    with pytest.raises(specs.SpecMissing):
        specs.round_qty("NOT-A-REAL-SYMBOL", 1.0)


def test_fee_constant_still_matches_the_exchange():
    """costs.py 的費率常數必須跟交易所實際費率一致。

    回測沒有幣種脈絡,只能用單一常數;但如果交易所哪天改了費率,
    這條測試要當場變紅,而不是讓回測繼續用一個過期的數字。
    """
    from portfolio import specs
    from portfolio.costs import TAKER_FEE_PCT
    from portfolio.paper import SYMBOLS
    for s in SYMBOLS:
        assert specs.taker_fee_pct(s) == pytest.approx(TAKER_FEE_PCT), \
            f"{s} 交易所費率 {specs.taker_fee_pct(s)}% 與常數 {TAKER_FEE_PCT}% 不符"


def test_slippage_moves_the_fill_price_and_is_not_booked_as_a_fee():
    """滑點必須改變成交價,不能被記成手續費。

    2026-09-09:原本把滑點與手續費加總,整包當 fee 從餘額扣掉。
    交易所不是這樣:手續費真的收一筆錢,滑點是成交價比預期差。
    後果不同 —— 滑點會改變 avg_price,而 avg_price 決定強平價、
    未實現盈虧、ROI。記錯地方等於開倉均價從一開始就是錯的。
    """
    from portfolio.costs import SLIP_FLOOR_PCT
    o = orders.Order(symbol="ETH-USDT", side="BUY", qty=1.0, price=2000.0,
                     notional=2000.0, reason="test")
    rec = execution.PaperExecutor().submit([o])[0]
    assert rec["status"] == "FILLED"
    # 買單成交價必須比參考價**貴**(對自己不利的方向)
    assert rec["price"] > 2000.0
    assert rec["price"] == pytest.approx(2000.0 * (1 + SLIP_FLOOR_PCT / 100))
    assert rec["ref_price"] == 2000.0
    # 名目要用實際成交價重算,不是下單時的參考價
    assert rec["notional"] == pytest.approx(1.0 * rec["price"])

    s = orders.Order(symbol="ETH-USDT", side="SELL", qty=1.0, price=2000.0,
                     notional=2000.0, reason="test")
    srec = execution.PaperExecutor().submit([s])[0]
    assert srec["price"] < 2000.0, "賣單必須成交在更低的價格"


def test_fee_uses_the_exchange_per_symbol_taker_rate():
    """手續費率必須來自交易所逐幣規格,不是自己寫的常數。"""
    from portfolio import specs
    from portfolio.costs import SLIP_FLOOR_PCT, TAKER_FEE_PCT
    # 手續費率不得把滑點混進去
    assert specs.taker_fee_pct("ETH-USDT") == pytest.approx(TAKER_FEE_PCT)
    assert specs.taker_fee_pct("ETH-USDT") != pytest.approx(
        TAKER_FEE_PCT + SLIP_FLOOR_PCT)


def test_full_close_uses_exact_held_quantity_and_leaves_no_dust():
    """全平倉必須用實際持有量,而且不得被任何門檻擋下。

    用權重反推數量會經過 notional/price 一來一回的浮點運算再無條件捨去,
    少一位就留下殘倉(例如 498 顆 XRP 只賣掉 497)。殘倉會繼續付資金費、
    繼續佔保證金,而且下一輪會被同樣的門檻再擋一次 —— 永遠清不掉。
    """
    eq, px = 10_000.0, 1.4168
    for amt in (498.0, 497.9999999, 2.0, 1.0, 0.5):
        held_w = {"XRP-USDT": amt * px / eq}
        os_ = orders.build_orders({}, held_w, eq, {"XRP-USDT": px},
                                  None, {"XRP-USDT": amt})
        assert os_, f"持有 {amt} 卻沒產生平倉單 —— 殘倉會清不掉"
        assert os_[0].side == "SELL"
        assert os_[0].qty == amt, f"持有 {amt} 但只平掉 {os_[0].qty}"


def test_full_close_of_a_short_buys_back_the_exact_amount():
    """空單全平要送 BUY,數量取絕對值。"""
    os_ = orders.build_orders({}, {"XRP-USDT": -0.07}, 10_000.0,
                              {"XRP-USDT": 1.4168}, None,
                              {"XRP-USDT": -498.0})
    assert os_ and os_[0].side == "BUY"
    assert os_[0].qty == 498.0


def test_partial_reduction_still_respects_exchange_precision():
    """減碼(非全平)仍要照交易所精度捨去 —— 不能因為上一條就整個放行。"""
    from portfolio import specs
    os_ = orders.build_orders({"UNI-USDT": 0.05}, {"UNI-USDT": 0.20},
                              10_000.0, {"UNI-USDT": 6.748},
                              None, {"UNI-USDT": 296.0})
    assert os_
    o = os_[0]
    assert o.side == "SELL"
    qp = specs.spec("UNI-USDT")["quantity_precision"]
    assert o.qty == pytest.approx(round(o.qty, qp), abs=1e-12)
    assert o.qty < 296.0, "減碼不是全平,不該賣掉全部"


# ══════════════════════════════════════════════════════════
# 六、部分成交(2026-09-09 加)
#
# 實測掛單簿:我們的單只佔前 20 檔深度的 0.009%~0.057%,這個規模下
# 部分成交實務上不會發生。但真正的風險不是部分成交本身,而是帳本記的
# 是「下單量」而不是「成交量」—— 接上真交易所後只要發生一次,帳上就
# 多出沒買到的部位,而訂單記錄顯示成功、帳本平衡、面板正常。
# ══════════════════════════════════════════════════════════
def test_paper_fill_reports_filled_qty():
    """紙上全額成交,但 filled_qty 這個欄位必須存在且正確。"""
    o = orders.Order(symbol="ETH-USDT", side="BUY", qty=0.5, price=2000.0,
                     notional=1000.0, reason="test")
    rec = execution.PaperExecutor().submit([o])[0]
    assert rec["status"] == "FILLED"
    assert rec["filled_qty"] == pytest.approx(0.5)


def test_partial_fill_is_flagged_and_not_treated_as_full():
    """部分成交要標成 PARTIAL,而且不能被當成全額成交。"""
    class HalfFill(execution.PaperExecutor):
        venue = "PAPER"

        def _place(self, order):
            d = super()._place(order)
            d["filled_qty"] = abs(order.qty) / 2      # 只成交一半
            return d

    o = orders.Order(symbol="ETH-USDT", side="BUY", qty=1.0, price=2000.0,
                     notional=2000.0, reason="test")
    rec = HalfFill().submit([o])[0]
    assert rec["status"] == "PARTIAL"
    assert rec["filled_qty"] == pytest.approx(0.5)
    assert rec["qty"] == pytest.approx(1.0), "下單量要原樣保留,供對帳"


def test_zero_fill_becomes_rejected_not_a_phantom_position():
    """完全沒成交必須是 REJECTED —— 不得在帳本留下幽靈部位。"""
    class NoFill(execution.PaperExecutor):
        venue = "PAPER"

        def _place(self, order):
            d = super()._place(order)
            d["filled_qty"] = 0.0
            return d

    o = orders.Order(symbol="ETH-USDT", side="BUY", qty=1.0, price=2000.0,
                     notional=2000.0, reason="test")
    rec = NoFill().submit([o])[0]
    assert rec["status"] == "REJECTED"


def test_ledger_records_filled_qty_not_ordered_qty():
    """帳本必須記實際成交量,手續費也要照實際成交量收。

    這是這一組測試真正在守的東西:記錯了,帳本會多出沒買到的部位,
    而且訂單記錄顯示成功、帳本平衡、面板正常,沒有一條檢查會發現。
    """
    from portfolio import specs
    from portfolio.account import Account
    a = Account()
    rec = {"symbol": "ETH-USDT", "side": "BUY", "qty": 1.0,
           "filled_qty": 0.4, "price": 2000.0, "status": "PARTIAL"}
    qty = float(rec["filled_qty"])
    px = float(rec["price"])
    fee = qty * px * specs.taker_fee_pct(rec["symbol"]) / 100.0
    a.fill(rec["symbol"], rec["side"], qty, px, fee, "t", leverage=1.0)
    p = a.positions["ETH-USDT"]
    assert p.position_amt == pytest.approx(0.4), "帳本記了沒成交的量"
    assert a.fee_paid == pytest.approx(0.4 * 2000.0 * 0.0005), \
        "手續費該照實際成交量收"


# ══════════════════════════════════════════════════════════
# 七、資金費用交易所實際結算值(2026-09-09 加)
# ══════════════════════════════════════════════════════════
def test_expected_settlement_count_matches_8h_schedule():
    """交易所固定 00/08/16 UTC 結算,應結算次數算得出來。"""
    from portfolio.specs import expected_settlements
    end = 1788940800000                     # 剛好是一個結算時點
    for hours, want in ((0, 0), (8, 1), (16, 2), (24, 3), (48, 6)):
        got = expected_settlements(end - hours * 3600 * 1000, end)
        assert got == want, f"過去 {hours} 小時應有 {want} 次,算出 {got}"


def test_stale_funding_cache_raises_instead_of_charging_zero():
    """資金費快取過期必須拋例外,**絕不可以安靜地回 0**。

    回 0 等於不收資金費 —— 帳本、面板、測試全部正常,而績效被靜靜
    美化。這正是「絕不用預設值吞掉錯誤」要擋的東西。
    """
    import time
    from portfolio.specs import SpecMissing, funding_rate_sum
    future = int(time.time() * 1000) + 2 * 24 * 3600 * 1000
    with pytest.raises(SpecMissing):
        funding_rate_sum("BTC-USDT", future - 24 * 3600 * 1000, future)


def test_funding_uses_real_settled_rates_not_estimates():
    """記帳用的必須是交易所實際結算值,而且可能與估計值不同號。

    實測 UNI:抽樣估計 +0.0033%(要付錢),交易所實際 −0.0236%
    (是收錢)—— 連方向都相反。
    """
    import json as _json
    from portfolio.specs import (FUNDING_CACHE, expected_settlements,
                                 funding_settlements)
    # 2026-09-09 修:原本用「現在」當右界,但快取每天 00:30 才更新一次,
    # 而交易所在 00/08/16 UTC 結算 —— 從早上 8 點起這條斷言必然失敗,
    # 而系統其實正常。憲法第八條:天天誤報的檢查等於沒有檢查。
    # 正確的問法是「快取寫入的那一刻它完整嗎」,右界用快取自己的時間戳。
    cache_ms = int(float(_json.loads(
        FUNDING_CACHE.read_text(encoding="utf-8"))["updated"]) * 1000)
    day = 24 * 3600 * 1000
    st = funding_settlements("BTC-USDT", cache_ms - day, cache_ms)
    want = expected_settlements(cache_ms - day, cache_ms)
    assert len(st) >= want, f"快取寫入時應有 {want} 次結算,只有 {len(st)}"
    for r in st:
        assert isinstance(r["rate"], float)
        assert abs(r["rate"]) <= 0.003, "超過交易所 ±0.3% 上下限"


def test_funding_window_is_half_open_so_reruns_dont_double_charge():
    """區間左開右閉 —— 重跑不得重複收同一筆結算。"""
    import time
    from portfolio.specs import funding_settlements
    now = int(time.time() * 1000)
    day = 24 * 3600 * 1000
    st = funding_settlements("BTC-USDT", now - day, now)
    assert st, "需要有結算才測得出來"
    boundary = st[0]["t"]
    # 以第一筆的時點當起點,那一筆就不該再被收一次
    again = funding_settlements("BTC-USDT", boundary, now)
    assert all(r["t"] > boundary for r in again), "邊界那筆被重複收了"
