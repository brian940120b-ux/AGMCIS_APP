"""
模擬帳戶卡 —— 一個來源,而且會自己跳 · 2026-09-18

執政官:「數字對不上,我只要一個,就是系統自行模擬的資訊,
並且我想看到即時的盈虧數字變化。」

═══ 為什麼會對不上 ═══
**因為我做了兩個來源。** 成績單的權益讀 `portfolio_equity.jsonl`
的最後一列 —— 那是每日 00:30 記帳當下、用當天收盤價算的;
模擬持倉卡的未實現則用**現在**的即時價。

兩個不同時間點的價格,當然是兩個數字。它們誰都沒有錯,
但擺在同一頁上就是一個沒有答案的問題:哪一個才是我現在的錢?

修法不是挑一個顯示,是**讓所有「現在」的數字出自同一組價格**。

═══ 這一組守的 ═══
一、所有即時數字來自同一個快照(同一組 marks、同一個瞬間)
二、基準跟帳本用**同一個公式**,只是餵不同的價格
三、只有歷史才知道的東西(回撤、進出次數)要標明不是即時的
四、問不到價的那一檔**完全不參與合計**,而且要說出來
五、連不上的時候畫面要說「數字是舊的」—— 凍住的數字跟活著的
    數字長得一模一樣
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scripts.dashboard as dash
from portfolio.account import Account, Position
from portfolio.scorecard import live

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


def acct(**kw):
    a = Account()
    a.balance = kw.get("balance", 9800.0)
    a.start_equity = 10000.0
    a.realized_pnl = kw.get("realized", 12.5)
    a.positions = kw.get("positions", {})
    a.bench_start = kw.get("bench_start", {})
    return a


def pos(sym, amt, avg, lev=3.0):
    return Position(symbol=sym, position_amt=amt, avg_price=avg, leverage=lev)


# ══════════════════════════════════════════════════════════
# 一、一個瞬間,一組價格
# ══════════════════════════════════════════════════════════
def test_every_live_number_comes_from_the_same_prices():
    """權益 = 錢包 + 未實現,而未實現就是逐檔加總。

    **這條就是「數字對不上」。** 以前權益來自帳本(00:30 的收盤價)、
    未實現來自即時價,所以 權益 ≠ 錢包 + 未實現,而畫面上看不出
    為什麼。現在兩個出自同一組 marks,恆等式必須成立。
    """
    a = acct(positions={"BTC-USDT": pos("BTC-USDT", 0.1, 70000.0),
                        "SOL-USDT": pos("SOL-USDT", 10.0, 200.0)})
    lv = live(a, {"BTC-USDT": 76000.0, "SOL-USDT": 190.0}, now=NOW)
    legs = sum(g["pnl"] for g in lv.legs.values())
    assert lv.unrealized_pnl == legs
    assert lv.equity == a.balance + lv.unrealized_pnl


def test_the_return_is_measured_against_the_starting_equity():
    import pytest
    a = acct(balance=10000.0, positions={})
    assert live(a, {}, now=NOW).return_pct == pytest.approx(0.0)
    a = acct(balance=11000.0, positions={})
    assert live(a, {}, now=NOW).return_pct == pytest.approx(10.0)


def test_the_benchmark_still_works_when_the_strategy_is_flat():
    """**空手正是最想看到基準的時候** —— 它回答「不交易的話,
    現在是賺是賠」,而那就是這套規則有沒有幫上忙的全部意思。

    第一版只餵持倉的價格,於是空手時基準整個算不出來。
    """
    a = acct(balance=10000.0, positions={},
             bench_start={"BTC-USDT": 100.0, "SOL-USDT": 200.0})
    lv = live(a, {"BTC-USDT": 110.0, "SOL-USDT": 180.0}, now=NOW)
    assert lv.benchmark_pct is not None
    assert lv.excess_pct is not None


def test_the_benchmark_uses_the_ledgers_own_formula():
    """**一個公式只寫一次。** 面板自己再寫一份,兩份遲早分岔 ——
    而分岔那天,畫面上的「贏基準」與帳本裡的不一樣,
    沒有人分得出哪一個是對的。那正是「數字對不上」的來源。"""
    from portfolio.paper import benchmark_pct
    a = acct(bench_start={"BTC-USDT": 70000.0, "SOL-USDT": 200.0})
    marks = {"BTC-USDT": 77000.0, "SOL-USDT": 220.0}
    lv = live(a, marks, now=NOW)
    want = benchmark_pct(a, marks, int(NOW.timestamp() * 1000))
    assert lv.benchmark_pct == want


def test_the_excess_is_the_difference_between_those_two():
    a = acct(balance=11000.0, bench_start={"BTC-USDT": 100.0})
    lv = live(a, {"BTC-USDT": 105.0}, now=NOW)
    assert abs(lv.excess_pct - (lv.return_pct - lv.benchmark_pct)) < 1e-9


# ══════════════════════════════════════════════════════════
# 二、問不到價的那一檔
# ══════════════════════════════════════════════════════════
def test_a_symbol_without_a_price_is_left_out_of_every_total():
    """**不准用開倉均價頂替。** 頂替會讓那一檔的盈虧顯示成 0,
    而 0 跟「不知道」在畫面上長得一樣,意思卻相反。"""
    a = acct(positions={"BTC-USDT": pos("BTC-USDT", 0.1, 70000.0),
                        "SOL-USDT": pos("SOL-USDT", 10.0, 200.0)})
    lv = live(a, {"BTC-USDT": 76000.0}, now=NOW)
    assert lv.missing == ["SOL-USDT"]
    assert lv.legs["SOL-USDT"]["pnl"] is None
    assert lv.unrealized_pnl == 600.0, "SOL 不該用均價頂替成 0 再加進來"


def test_the_card_says_the_total_is_short_when_a_price_is_missing():
    html = render_with(
        acct(positions={"BTC-USDT": pos("BTC-USDT", 0.1, 70000.0),
                        "SOL-USDT": pos("SOL-USDT", 10.0, 200.0)}),
        {"BTC-USDT": 76000.0})
    assert "問不到現價" in html
    assert "合計因此是偏少的" in html


# ══════════════════════════════════════════════════════════
# 三、卡片本身
# ══════════════════════════════════════════════════════════
def render_with(a, marks):
    from portfolio.scorecard import score
    dash._CACHE["sim"] = (time.time(),
                          {"live": live(a, marks, now=NOW), "card": score()})
    return dash.block_sim()


def test_the_live_numbers_carry_ids_so_they_can_be_updated_in_place():
    """沒有 id 就換不掉,「即時」就只是每次重整一次。"""
    html = render_with(acct(positions={"BTC-USDT": pos("BTC-USDT", .1, 70000.)}),
                       {"BTC-USDT": 76000.0})
    for el in ("s-eq", "s-ret", "s-exc", "s-unr", "s-rea", "s-legs", "s-at"):
        assert f'id="{el}"' in html, f"少了 {el},JS 換不掉這一格"


def test_the_historical_numbers_are_labelled_as_not_live():
    """回撤與進出次數本來就是一段時間累積的 —— 不標的話,
    使用者會以為整張卡都是即時的,然後懷疑為什麼它們不動。"""
    html = render_with(acct(), {})
    assert "每日記帳的帳本" in html
    assert "它們不是即時的" in html


def test_the_card_is_labelled_as_the_simulation_not_the_users_money():
    html = render_with(acct(), {})
    assert "模擬金" in html and "系統自己下單" in html


def test_an_empty_account_says_flat_is_a_position():
    assert "空手是一個部位" in render_with(acct(), {})


# ══════════════════════════════════════════════════════════
# 四、輪詢回來的跟畫面上的是同一套
# ══════════════════════════════════════════════════════════
def test_the_poll_and_the_page_share_one_snapshot_function():
    """/api/sim 回的必須是 block_sim() 用的同一個 sim_snapshot() ——
    各算各的話,畫面上的數字與輪詢回來的會慢慢分開。"""
    src = Path(dash.__file__).read_text(encoding="utf-8")
    api = src[src.index('if u.path.startswith("/api/sim")'):][:900]
    assert "sim_snapshot()" in api
    assert "sim_snapshot()" in src[src.index("def block_sim("):][:1200]


def test_the_page_says_so_when_the_poll_cannot_reach_the_server():
    """**凍住的數字跟活著的數字長得一模一樣。**"""
    page = dash.render()
    assert "連不上,數字是舊的" in page


def test_the_poll_interval_is_declared_on_screen():
    assert "每 5 秒" in render_with(acct(), {})


def test_the_prices_come_from_one_bulk_call_not_one_per_symbol():
    """**一輪一次請求,不是一個幣一次。**

    速率預算是 2 次/秒 = 120 次/分。7 個幣各打一次 K 線、5 秒一輪
    = 84 次/分,光面板就吃掉七成,日常記帳、巡檢、交易所查詢全部
    要跟它搶。一次拿回全部的話,5 秒一輪只要 12 次/分。

    端點的形狀是 scripts/probe_price_feed.py 2026-09-18 在 VPS 實跑
    量到的(不帶 symbol -> code 0,1041 筆陣列,七個幣全在),
    **不是猜的**。
    """
    src = Path(dash.__file__).read_text(encoding="utf-8")
    body = src[src.index("def sim_marks("):src.index("def sim_snapshot(")]
    assert "all_prices()" in body
    assert "klines(" not in body, "sim_marks 不該再逐幣打 K 線"


def test_a_changed_response_shape_is_reported_not_papered_over():
    """交易所哪天改了形狀,要說形狀變了 —— 不要假裝拿到了價格。"""
    src = Path(dash.__file__).read_text(encoding="utf-8")
    body = src[src.index("def all_prices("):src.index("def sim_marks(")]
    assert "data 不是陣列" in body


def test_the_screen_shows_how_old_the_quote_actually_is():
    """「每 5 秒更新」只是我們問的頻率;交易所那筆價格本身有多舊是
    另一回事,而那才是「即時」的真正尺度。

    說不出年齡的「即時」是一句沒有證據的話。
    """
    page = dash.render()
    assert "行情 ' + d.age_s.toFixed(1) + ' 秒前" in page


def test_the_emitted_javascript_has_a_valid_thousands_separator():
    """這一段在 f-string 裡,反斜線要寫兩個。寫一個的話 Python 端是
    無效跳脫序列,而 JS 端拿到的正規表示式會是壞的。"""
    page = dash.render()
    m = re.search(r"var t = Math\.abs.*", page)
    assert m and r"/\B(?=(\d{3})+(?!\d))/g" in m.group(0), m and m.group(0)


# ══════════════════════════════════════════════════════════
# 五、只做多這件事要寫在畫面上
# ══════════════════════════════════════════════════════════
def test_the_card_says_the_live_strategy_is_long_only():
    """2026-09-18 執政官:「除了做多有做空嗎?」

    現在的答案是**沒有**。不寫在畫面上的話,看到持倉列上那個「多」
    標籤的人,會以為那是**這一筆**的方向,而不是系統唯一做得出來
    的方向。
    """
    html = render_with(acct(), {})
    assert "只做多" in html
    assert "空手" in html and "不做空" in html


def test_the_long_short_rule_is_wired_into_the_research_grid():
    """`ma_long_short` 2026-09-08 就寫好了,附了完整的預先登記理由,
    然後**沒有任何地方呼叫它** —— registry() 裡沒有,研究迴路也沒試過。

    寫好卻沒接上的東西不會自己生效,它只會在某天被發現的時候,
    讓人以為系統早就在做那件事了。
    """
    from portfolio.research import Variant, grid
    kinds = {v.kind for v in grid(Variant(ma=50, vol_target_pct=27.0,
                                          leverage_cap=3.0))}
    assert "longshort" in kinds


def test_the_research_runner_can_actually_build_the_long_short_rule():
    """接進網格還不夠 —— 跑的人要真的認得那個 kind,
    否則它會被當成均線跑一遍,而表格上看起來一切正常。"""
    import importlib.util
    from portfolio.research import Variant
    spec = importlib.util.spec_from_file_location(
        "rsrch", Path(__file__).resolve().parents[1] / "scripts/research.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    ls = m.weights_fn(Variant(ma=50, vol_target_pct=27.0, leverage_cap=3.0,
                              kind="longshort"))
    ma = m.weights_fn(Variant(ma=50, vol_target_pct=27.0, leverage_cap=3.0))

    # 造一段資料:X 在均線之下 —— 多空版該是負權重,均線版該是空手
    from datetime import datetime, timedelta, timezone

    from portfolio.sim import Bar
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    dates = [t0 + timedelta(days=i) for i in range(60)]
    closes = [100.0] * 59 + [50.0]
    idx = {s: {dates[i]: Bar(dates[i], c, c, c, c, 0.0)
               for i, c in enumerate(closes)} for s in m.SYMBOLS}
    w_ls = ls(59, dates, idx)
    w_ma = ma(59, dates, idx)
    assert w_ls and all(v < 0 for v in w_ls.values()), \
        f"跌破均線,多空版該做空,拿到 {w_ls}"
    assert not w_ma, f"跌破均線,現任該空手,拿到 {w_ma}"
