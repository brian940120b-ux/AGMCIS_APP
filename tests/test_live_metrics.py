"""
第三十節 Agent 10(Performance Analyst)與第六十一節。

Sharpe、Sortino、MFE、MAE、Holding Time 原本只有回測算得出來,
而回測答不出「**真正發生過的**交易,報酬對得起它的波動嗎」。

一個貫穿全部的原則:**算不出來回 None,不回 0。**
儀表板上「—」與「0」必須不一樣。
"""
import math
import statistics

import pytest

import position_monitor
from agmcis.review import live_metrics


def a_trade(pnl, margin=100.0, hours=4.0, mfe=None, mae=None, status="CLOSED"):
    from datetime import datetime, timedelta

    opened = datetime(2026, 1, 1, 0, 0)
    return {
        "status": status,
        "pnl_usdt": pnl,
        "size_usdt": margin,
        "opened_at": opened.isoformat(),
        "closed_at": (opened + timedelta(hours=hours)).isoformat(),
        "max_favourable_pct": mfe,
        "max_adverse_pct": mae,
    }


def many(count, pnl, **kwargs):
    return [a_trade(pnl, **kwargs) for _ in range(count)]


# ---------------- 樣本門檻 ----------------

def test_sharpe_needs_enough_trades():
    """
    一個用五筆交易算出來的 Sharpe 2.4,比沒有這個數字更誤導。
    """
    assert live_metrics.sharpe([a_trade(10), a_trade(-5)]) is None


def test_sharpe_is_computed_once_the_sample_is_large_enough():
    trades = many(live_metrics.MIN_SAMPLE, 10) + many(live_metrics.MIN_SAMPLE, -5)
    assert live_metrics.sharpe(trades) is not None


def test_the_sample_threshold_matches_attribution():
    """
    兩邊用不同門檻,會讓同一批交易在兩份報告裡一份可信一份不可信。
    """
    from agmcis.review.attribution import MIN_SAMPLE

    assert live_metrics.MIN_SAMPLE == MIN_SAMPLE


# ---------------- Sharpe / Sortino 的性質 ----------------

def test_identical_returns_give_no_sharpe_not_infinity():
    """標準差為 0 時回 None,不回無限大。"""
    assert live_metrics.sharpe(many(live_metrics.MIN_SAMPLE, 10)) is None


def test_sortino_is_none_without_any_losing_trade():
    """
    無限大排序永遠第一,而「還沒虧過」通常只代表樣本不夠長。
    """
    trades = many(live_metrics.MIN_SAMPLE, 10) + many(live_metrics.MIN_SAMPLE, 20)
    assert live_metrics.sortino(trades) is None


def test_sortino_is_kinder_than_sharpe_to_upside_volatility():
    """
    往上的波動不是風險。一個常常大賺的策略在 Sharpe 上會被處罰,
    在 Sortino 上不會。
    """
    # 大部分小賺、偶爾大賺、少數小虧
    trades = (
        many(30, 5) + many(5, 200) + many(10, -5)
    )

    assert live_metrics.sortino(trades) > live_metrics.sharpe(trades)


def test_returns_use_margin_not_absolute_pnl():
    """
    一筆在 1000 USDT 帳戶上賺 50 的交易,跟在 10000 上賺 50 的,
    風險調整後不是同一件事。
    """
    small = live_metrics._returns([a_trade(50, margin=1000)])
    large = live_metrics._returns([a_trade(50, margin=10000)])

    assert small[0] > large[0]


def test_a_trade_without_margin_is_excluded_not_counted_as_zero():
    """分母不知道的時候,報酬率沒有答案。"""
    values = live_metrics._returns([
        a_trade(10, margin=100), a_trade(10, margin=None), a_trade(10, margin=0),
    ])

    assert len(values) == 1


def test_sharpe_matches_the_textbook_formula():
    """跟手算的結果對一次,不然這個數字只是「程式跑出來的東西」。"""
    trades = [a_trade(pnl, margin=100) for pnl in ([10] * 15 + [-5] * 15)]

    values = [0.10] * 15 + [-0.05] * 15
    expected = statistics.fmean(values) / statistics.pstdev(values)

    assert live_metrics.sharpe(trades) == pytest.approx(expected)


def test_sortino_matches_the_textbook_formula():
    trades = [a_trade(pnl, margin=100) for pnl in ([10] * 15 + [-5] * 15)]

    values = [0.10] * 15 + [-0.05] * 15
    downside = [v for v in values if v < 0]
    expected = statistics.fmean(values) / math.sqrt(
        statistics.fmean([v ** 2 for v in downside]))

    assert live_metrics.sortino(trades) == pytest.approx(expected)


# ---------------- 持倉時間 ----------------

def test_holding_time_reports_both_mean_and_median():
    """
    少數幾筆放了一個月的交易會把平均拉得很難看,而那兩個數字
    差很多本身就是一個訊號。
    """
    trades = many(9, 10, hours=1.0) + [a_trade(10, hours=100.0)]

    average, median, count = live_metrics.holding_hours(trades)

    assert count == 10
    assert median == 1.0
    assert average > median


def test_a_trade_with_reversed_timestamps_is_excluded():
    """取絕對值會讓一筆時間戳寫反的交易看起來正常。"""
    average, median, count = live_metrics.holding_hours([a_trade(10, hours=-5)])

    assert count == 0
    assert average is None


def test_missing_timestamps_do_not_become_zero_hours():
    trade = a_trade(10)
    trade["closed_at"] = None

    _, _, count = live_metrics.holding_hours([trade])
    assert count == 0


# ---------------- MFE / MAE ----------------

def test_unmeasured_excursions_are_excluded_not_zero():
    """
    migration 010 之前開的倉沒有量測過。把它們當成 0,
    「這筆從來沒有浮虧」會變成統計事實。
    """
    trades = [
        a_trade(10, mfe=15.0, mae=-3.0),
        a_trade(10, mfe=None, mae=None),
    ]

    stats = live_metrics.excursions(trades)

    assert stats["avg_mfe_pct"] == 15.0
    assert stats["measured"] == 1
    assert stats["total"] == 2


def test_the_worst_mae_is_the_most_negative_one():
    """虧損單的 MAE 回答「停損是不是設得太緊」。"""
    stats = live_metrics.excursions([
        a_trade(-5, mae=-8.0), a_trade(-5, mae=-25.0), a_trade(10, mae=-1.0),
    ])

    assert stats["worst_mae_pct"] == -25.0


def test_no_measurements_at_all_gives_none():
    stats = live_metrics.excursions([a_trade(10), a_trade(-5)])

    assert stats["avg_mfe_pct"] is None
    assert stats["measured"] == 0


# ---------------- compute() ----------------

def test_compute_covers_every_metric_agent_10_lists():
    """第三十節 Agent 10 列了十一項。這一條盯它們都在。"""
    payload = live_metrics.compute(many(5, 10) + many(5, -5))

    for key in ("win_rate", "profit_factor", "expectancy_usdt",
                "sharpe", "sortino", "avg_win", "avg_loss",
                "avg_mfe_pct", "avg_mae_pct", "avg_holding_hours"):
        assert key in payload, f"少了 {key}"


def test_compute_ignores_open_trades():
    payload = live_metrics.compute([
        a_trade(10), a_trade(None, status="OPEN"),
    ])

    assert payload["trades"] == 1


def test_compute_says_when_the_sample_is_too_small():
    payload = live_metrics.compute([a_trade(10)])

    assert payload["reliable"] is False
    assert payload["min_sample"] == live_metrics.MIN_SAMPLE


def test_an_empty_history_gives_none_not_zero():
    payload = live_metrics.compute([])

    assert payload["trades"] == 0
    assert payload["win_rate"] is None
    assert payload["sharpe"] is None


def test_average_loss_is_negative():
    """
    平均虧損顯示成正數會讓「平均賺 10、平均虧 5」讀起來像兩邊都賺。
    """
    payload = live_metrics.compute(many(5, 10) + many(5, -5))

    assert payload["avg_win"] == 10.0
    assert payload["avg_loss"] == -5.0


# ---------------- position_monitor 記錄偏移 ----------------

def test_a_long_position_measures_the_high_as_favourable():
    favourable, adverse = position_monitor._excursion(
        "LONG", entry_price=100, leverage=5, high=110, low=95, price=105)

    assert favourable == 50.0     # (110-100)/100 * 5 * 100
    assert adverse == -25.0       # (95-100)/100 * 5 * 100


def test_a_short_position_measures_the_low_as_favourable():
    favourable, adverse = position_monitor._excursion(
        "SHORT", entry_price=100, leverage=5, high=110, low=95, price=105)

    assert favourable == 25.0
    assert adverse == -50.0


def test_a_candle_entirely_above_entry_gives_zero_adverse_not_a_positive():
    """
    一根完全在進場價之上的 K 棒,對做多而言 MAE 是 0
    (從來沒有虧過),不是一個正數。
    """
    favourable, adverse = position_monitor._excursion(
        "LONG", entry_price=100, leverage=1, high=110, low=105, price=108)

    assert adverse == 0.0
    assert favourable == 10.0


def test_without_a_candle_it_falls_back_to_the_polled_price():
    """
    量到的會比真實值保守,而保守的方向在這裡是安全的 ——
    它會讓「停損設得太緊」這個結論比較不容易成立。
    """
    favourable, adverse = position_monitor._excursion(
        "LONG", entry_price=100, leverage=1, high=None, low=None, price=102)

    assert favourable == 2.0
    assert adverse == 0.0


def test_an_unknown_direction_measures_nothing():
    assert position_monitor._excursion(
        "SIDEWAYS", 100, 1, 110, 95, 105) == (None, None)


def test_a_zero_entry_price_measures_nothing():
    assert position_monitor._excursion("LONG", 0, 1, 110, 95, 105) == (None, None)


def test_excursion_leverage_matches_the_roi_convention():
    """
    MFE / MAE 含槓桿,與 Position.roi_pct 同一個慣例 ——
    兩者用不同的基準,擺在同一張表上會互相矛盾。
    """
    from agmcis.core.enums import Direction, MarketType
    from agmcis.core.models import Position

    favourable, _ = position_monitor._excursion(
        "LONG", entry_price=100, leverage=5, high=110, low=100, price=110)

    reference = Position(
        symbol="X", market_type=MarketType.PERPETUAL,
        direction=Direction.LONG, entry_price=100.0,
        size_usdt=100.0, leverage=5.0,
    )

    assert favourable == pytest.approx(reference.roi_pct(110.0))


def test_a_failing_excursion_write_does_not_break_the_monitor(caplog):
    """
    這是一個統計欄位,而停損是保護 —— 一個因為寫不了統計而跳過
    停損檢查的監控,比沒有統計糟得多。
    """
    import logging
    from unittest.mock import patch

    import database_service

    with patch.object(database_service, "update_excursion",
                      side_effect=RuntimeError("no such column")), \
         caplog.at_level(logging.WARNING):
        position_monitor._track_excursion(
            {"id": 1, "symbol": "BTC/USDT", "entry_price": 100, "leverage": 5},
            "LONG", high=110, low=95, price=105,
        )

    assert "EXCURSION_WRITE_FAILED" in caplog.text


def test_excursion_is_recorded_before_the_exit_decision():
    """
    一筆這一輪被停損的交易,它的 MAE 也要含這一輪的極端值 ——
    否則「停損是不是設得太緊」那個問題會少掉最關鍵的一筆資料。
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(
        inspect.getsource(position_monitor.run_position_monitor)))

    order = sorted(
        (node.lineno, node.func.id)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in ("_track_excursion", "_exit_reason")
    )

    assert [name for _, name in order] == ["_track_excursion", "_exit_reason"]
