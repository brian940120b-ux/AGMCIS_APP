"""
第九十五節:Dashboard Lite 不再是一個 God Function。

原本 main.py 的 /dashboard handler 同時做三件事:抓現價、算損益、
組 HTML。這一組測試盯的是拆開之後**行為沒有變**,以及那個拆開
真正要修掉的東西 —— 損益公式的第二份拷貝。
"""
from unittest.mock import patch

import pytest

from api import dashboard_rows


def a_trade(**overrides):
    trade = {
        "symbol": "BTC/USDT", "signal": "LONG", "status": "OPEN",
        "entry_price": 100.0, "size_usdt": 200.0, "leverage": 5.0,
        "stoploss": 97.0, "takeprofit": 110.0, "opened_at": "2026-01-01",
    }
    trade.update(overrides)
    return trade


# ---------------- 只有一份損益公式 ----------------

def test_roi_matches_the_position_model_exactly():
    """
    這是拆開這個函式**真正的理由**。舊版自己算
    `(current - entry) / entry * leverage`,而正本在
    agmcis/core/models.py。兩份公式只會有一份被修到。
    """
    from agmcis.core.enums import Direction, MarketType
    from agmcis.core.models import Position

    trade = a_trade()
    row = dashboard_rows.build_row(trade, price_of=lambda s: 110.0)

    reference = Position(
        symbol="BTC/USDT", market_type=MarketType.PERPETUAL,
        direction=Direction.LONG, entry_price=100.0,
        size_usdt=200.0, leverage=5.0,
    )

    assert row["roi_pct"] == round(reference.roi_pct(110.0), 2)
    assert row["upnl_usdt"] == round(reference.unrealized_pnl(110.0), 2)


def test_a_short_position_profits_when_price_falls():
    row = dashboard_rows.build_row(
        a_trade(signal="SHORT", stoploss=103.0, takeprofit=90.0),
        price_of=lambda s: 90.0,
    )

    assert row["upnl_usdt"] > 0
    assert row["sign"] == "pos"


def test_notional_is_margin_times_leverage():
    """
    舊系統把保證金與名目價值混用,已實現與未實現損益因此差了 N 倍。
    """
    row = dashboard_rows.build_row(a_trade(), price_of=lambda s: 100.0)
    assert row["notional"] == 1000.0


# ---------------- 沒有數字時 ----------------

def test_no_price_means_none_not_zero():
    """
    「不知道」與「打平」是兩件事。回 0 會讓看不到價格的部位
    顯示成「打平」。
    """
    row = dashboard_rows.build_row(a_trade(), price_of=lambda s: None)

    assert row["roi_pct"] is None
    assert row["upnl_usdt"] is None
    assert row["sign"] == ""


def test_a_price_lookup_failure_does_not_kill_the_whole_table(caplog):
    def boom(symbol):
        raise RuntimeError("行情連不上")

    rows = dashboard_rows.build_rows(
        [a_trade(symbol="A/USDT"), a_trade(symbol="B/USDT")], price_of=boom,
    )

    assert len(rows) == 2
    assert all(row["roi_pct"] is None for row in rows)


def test_a_bad_direction_is_logged_not_swallowed(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        row = dashboard_rows.build_row(
            a_trade(signal="SIDEWAYS"), price_of=lambda s: 110.0,
        )

    assert row["roi_pct"] is None
    assert row["upnl_usdt"] is None
    assert "損益算不出來" in caplog.text


# ---------------- 裸倉 ----------------

def test_a_position_without_a_stop_is_flagged():
    """
    沒有停損的部位沒有虧損上限。這是這張表上唯一需要立刻行動的資訊,
    所以它有自己的旗標,不是靠看空欄位。
    """
    row = dashboard_rows.build_row(a_trade(stoploss=None), price_of=lambda s: 110.0)
    assert row["naked"] is True


def test_a_closed_trade_without_a_stop_is_not_naked():
    """已經平掉的部位不需要停損。把它算成裸倉會製造假警報。"""
    row = dashboard_rows.build_row(
        a_trade(status="CLOSED", stoploss=None, exit_price=110.0),
        price_of=lambda s: 999.0,
    )
    assert row["naked"] is False


def test_a_closed_trade_uses_its_exit_price_not_the_live_one():
    """對一筆已平倉的交易查現價沒有意義,而且會白打一次 API。"""
    calls = []

    def price_of(symbol):
        calls.append(symbol)
        return 999.0

    row = dashboard_rows.build_row(
        a_trade(status="CLOSED", exit_price=110.0, pnl_usdt=100.0),
        price_of=price_of,
    )

    assert calls == []
    assert row["current"] == 110.0


# ---------------- context ----------------

def _context(open_trades=None, closed_trades=None, account=None, price=110.0):
    import main

    with patch.object(main, "get_account",
                      return_value=account or {"balance": 10000, "trades": 10, "wins": 6}), \
         patch.object(main, "get_open_trades", return_value=open_trades or []), \
         patch.object(main, "get_closed_trades", return_value=closed_trades or []), \
         patch("agmcis.data.market_data.get_price", return_value=price):
        return main.dashboard_context()


def test_context_counts_naked_positions():
    context = _context(open_trades=[
        a_trade(symbol="A/USDT", stoploss=None),
        a_trade(symbol="B/USDT", stoploss=97.0),
    ])

    assert context["naked_count"] == 1


def test_context_only_renders_the_last_ten_closed_trades():
    """
    整個歷史放進一張同步渲染的表,會讓這一頁隨交易數量線性變慢。
    """
    closed = [
        a_trade(status="CLOSED", exit_price=110.0, pnl_usdt=i, symbol=f"S{i}/USDT")
        for i in range(30)
    ]
    context = _context(closed_trades=closed)

    assert len(context["closed_rows"]) == 10
    assert context["closed_rows"][-1]["symbol"] == "S29/USDT"


def test_win_rate_is_zero_when_there_are_no_trades():
    """除以零。舊版有一模一樣的守衛,拆開之後不能弄丟。"""
    context = _context(account={"balance": 100, "trades": 0, "wins": 0})
    assert context["win_rate"] == 0


def test_net_pnl_sign_drives_the_colour():
    context = _context(closed_trades=[
        a_trade(status="CLOSED", exit_price=90.0, pnl_usdt=-50.0),
    ])

    assert context["net_pnl"] == -50.0
    assert context["net_sign"] == "neg"


# ---------------- 渲染 ----------------

def _render(context):
    import main
    return main.templates.get_template("dashboard_lite.html").render(**context)


def test_the_page_renders_both_tables():
    html = _render(_context(
        open_trades=[a_trade(symbol="OPEN/USDT")],
        closed_trades=[a_trade(status="CLOSED", symbol="SHUT/USDT",
                               exit_price=110.0, pnl_usdt=20.0)],
    ))

    assert "OPEN/USDT" in html
    assert "SHUT/USDT" in html


def test_a_naked_position_gets_a_warning_at_the_top_of_the_page():
    html = _render(_context(open_trades=[a_trade(stoploss=None)]))

    assert "沒有停損" in html
    # 警告要在表格之前 —— 它是唯一需要立刻行動的東西。
    assert html.index("沒有停損") < html.index("目前持倉")


def test_an_empty_table_says_so_instead_of_rendering_nothing():
    html = _render(_context())
    assert "目前沒有資料" in html


def test_a_missing_stop_shows_a_warning_in_the_row_not_a_dash():
    html = _render(_context(open_trades=[a_trade(stoploss=None)]))
    assert "⚠️ 無" in html


def test_an_unknown_roi_renders_as_a_dash_not_zero():
    """
    「不知道」與「打平」在畫面上必須不一樣。
    """
    html = _render(_context(open_trades=[a_trade()], price=None))

    # 表格列裡的 ROI 與 UPNL 兩格
    assert html.count('<td class="">—</td>') >= 2
    assert "0.0%</td>" not in html


# ---------------- 沒有第二份公式了 ----------------

def test_main_no_longer_computes_pnl_itself():
    """
    靜態保證。測試會漏掉新增的分支,但這一條會擋住整類回歸:
    只要有人在 main.py 裡再算一次損益,它就會紅。
    """
    from pathlib import Path

    source = Path("main.py").read_text(encoding="utf-8")

    for forbidden in ("is_long(", "is_short(", "/ entry", "* lev"):
        assert forbidden not in source, f"main.py 不該再自己算損益({forbidden})"


def test_main_no_longer_builds_html_by_hand():
    from pathlib import Path

    source = Path("main.py").read_text(encoding="utf-8")

    assert "<td>" not in source, "表格 HTML 應該在 template 裡"
    assert "<tr>" not in source


# ---------------- analytics 的 God Function ----------------
#
# get_trade_analytics 原本 147 行,自己做完損益、逐標的、資金曲線、
# 回撤、最佳最差標的全部的事。拆開之後每一塊都可以單獨測 ——
# 而「可以單獨測」正是第九十五節那條規則想換到的東西。

import analytics


def _closed(pnl, symbol="BTC/USDT"):
    return {"status": "CLOSED", "symbol": symbol, "pnl_usdt": pnl}


def test_pnl_summary_is_a_pure_function():
    totals = analytics.summarise_pnl([_closed(10), _closed(-5), _closed(20)])

    assert totals["total_trades"] == 3
    assert totals["total_pnl"] == 25
    assert totals["win_rate"] == pytest.approx(200 / 3)
    assert totals["avg_win"] == 15
    assert totals["avg_loss"] == -5


def test_risk_reward_is_zero_not_infinite_without_losses():
    """無限大在排序與顯示上都會出事。"""
    totals = analytics.summarise_pnl([_closed(10), _closed(20)])
    assert totals["risk_reward_ratio"] == 0


def test_symbol_stats_are_sorted_by_net_pnl():
    rows = analytics.summarise_by_symbol([
        _closed(5, "A/USDT"), _closed(-20, "B/USDT"), _closed(30, "C/USDT"),
    ])

    assert [row["symbol"] for row in rows] == ["C/USDT", "A/USDT", "B/USDT"]


def test_symbol_wins_and_losses_always_add_up_to_trades():
    """
    打平的那一筆算在 losses 裡。改成第三類會讓
    wins + losses != trades,而前端假設它們相等。
    """
    rows = analytics.summarise_by_symbol([
        _closed(5), _closed(0), _closed(-5),
    ])

    row = rows[0]
    assert row["wins"] + row["losses"] == row["trades"] == 3


def test_analytics_assembles_from_the_extracted_parts():
    """組裝之後的結果要跟各部分算出來的一致。"""
    trades = [_closed(10, "A/USDT"), _closed(-4, "B/USDT")]

    with patch.object(analytics, "load_trades", return_value=trades), \
         patch.object(analytics, "load_account", return_value={"balance": 10006}), \
         patch.object(analytics, "split_by_pnl_basis", return_value=(trades, [])):
        result = analytics.get_trade_analytics()

    totals = analytics.summarise_pnl(trades)

    assert result["total_trades"] == totals["total_trades"]
    assert result["total_pnl"] == round(totals["total_pnl"], 2)
    assert result["best_symbol"] == "A/USDT"
    assert result["worst_symbol"] == "B/USDT"


def test_no_comparable_trades_keeps_the_old_empty_shape():
    """
    前端與 Telegram 日報直接讀這些欄位。這一輪只拆函式,不改契約。
    """
    with patch.object(analytics, "load_trades", return_value=[]), \
         patch.object(analytics, "load_account", return_value={"balance": 10000}), \
         patch.object(analytics, "split_by_pnl_basis", return_value=([], [])):
        result = analytics.get_trade_analytics()

    assert result["total_trades"] == 0
    assert result["best_symbol"] == "-"
    assert result["equity_curve"] == [analytics.START_BALANCE]
    assert result["pnl_basis"] == "LEVERAGED"


def test_legacy_trades_stay_out_of_the_main_numbers_by_default():
    """
    兩種損益基準混在一起算出來的數字兩邊都不是。
    """
    comparable = [_closed(10, "A/USDT")]
    legacy = [_closed(1000, "OLD/USDT")]

    with patch.object(analytics, "load_trades", return_value=comparable + legacy), \
         patch.object(analytics, "load_account", return_value={"balance": 10010}), \
         patch.object(analytics, "split_by_pnl_basis",
                      return_value=(comparable, legacy)):
        result = analytics.get_trade_analytics()

    assert result["total_trades"] == 1
    assert result["pnl_basis"] == "LEVERAGED"
    # 但舊資料不是消失 —— 它在自己的區塊裡,而且標明不可比較。
    assert result["legacy"]["count"] == 1
    assert result["legacy"]["comparable"] is False
