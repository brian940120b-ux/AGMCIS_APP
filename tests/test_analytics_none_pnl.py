"""
一筆 CLOSED 但 pnl_usdt 是 NULL 的交易,會讓四個函式拋 TypeError。

起因是 `trade.get("pnl_usdt", 0)`:欄位**存在但值是 None** 的時候,
`.get(key, default)` 回的是 None 不是 default —— 預設值永遠用不到。
然後 `equity += pnl` 就炸了,而那個例外會讓 `/api/analytics_pro`、
儀表板的分析區塊與 Telegram 日報整個掛掉。

這種資料列怎麼來的?平倉寫到一半當機、手動改過的資料列、
或從別處匯入的歷史。少見,不是不可能。
"""
from unittest.mock import patch

import pytest

import analytics
import analytics_report


def closed(pnl, symbol="BTC/USDT", basis="LEVERAGED"):
    return {
        "status": "CLOSED", "symbol": symbol,
        "pnl_usdt": pnl, "pnl_basis": basis,
    }


ONE_GOOD_ONE_NULL = [closed(10.0), closed(None, "ETH/USDT")]


# ---------------- 四個函式都不再炸 ----------------

@pytest.mark.parametrize("fn", [
    analytics.build_equity_curve,
    analytics.summarise_pnl,
    analytics.summarise_by_symbol,
    analytics.calculate_profit_factor,
])
def test_a_null_pnl_no_longer_crashes(fn):
    fn(ONE_GOOD_ONE_NULL)      # 不拋例外就是通過


def test_pnl_of_returns_none_for_a_missing_value():
    assert analytics.pnl_of(closed(None)) is None
    assert analytics.pnl_of({}) is None
    assert analytics.pnl_of(closed("abc")) is None
    assert analytics.pnl_of(closed(10)) == 10.0


# ---------------- 排除而不是當成 0 ----------------

def test_an_unknown_pnl_is_excluded_not_counted_as_break_even():
    """
    當成 0 更糟:它會安靜地進統計,讓「已實現收益」少算、
    讓勝率的分母多一個非勝利。
    """
    totals = analytics.summarise_pnl(ONE_GOOD_ONE_NULL)

    assert totals["total_trades"] == 1
    assert totals["total_pnl"] == 10.0
    assert totals["win_rate"] == 100.0      # 不是 50.0


def test_the_equity_curve_skips_unknown_pnl():
    """
    當成 0 會在曲線上畫出一段「這裡什麼都沒發生」,
    而實際上是「這裡發生了什麼我們不知道」。
    """
    curve = analytics.build_equity_curve(ONE_GOOD_ONE_NULL)

    assert curve == [analytics.START_BALANCE, analytics.START_BALANCE + 10.0]


def test_symbol_stats_skip_the_unknown_one():
    rows = analytics.summarise_by_symbol(ONE_GOOD_ONE_NULL)

    assert [row["symbol"] for row in rows] == ["BTC/USDT"]


def test_profit_factor_ignores_the_unknown_one():
    assert analytics.calculate_profit_factor(
        [closed(10.0), closed(-5.0), closed(None)]) == 2.0


def test_with_pnl_reports_how_many_it_dropped():
    """加總對不起來會讓人以為統計算錯。"""
    usable, skipped = analytics.with_pnl(ONE_GOOD_ONE_NULL)

    assert len(usable) == 1
    assert skipped == 1


def test_the_analytics_payload_says_how_many_were_excluded():
    trades = ONE_GOOD_ONE_NULL

    with patch.object(analytics, "load_trades", return_value=trades), \
         patch.object(analytics, "load_account", return_value={"balance": 10010}):
        stats = analytics.get_trade_analytics()

    assert stats["unpriced_trades"] == 1
    assert stats["total_trades"] == 1


def test_the_empty_payload_has_the_same_shape():
    """
    兩條回傳路徑的欄位必須一樣,否則呼叫端要為「沒有交易」寫特例。
    """
    with patch.object(analytics, "load_trades", return_value=[closed(None)]), \
         patch.object(analytics, "load_account", return_value={"balance": 10000}):
        stats = analytics.get_trade_analytics()

    assert stats["total_trades"] == 0
    assert stats["unpriced_trades"] == 1


def test_no_module_still_uses_the_broken_get_default():
    """
    靜態保證。`.get("pnl_usdt", 0)` 是一個看起來安全但不安全的寫法,
    而它會再長回來。
    """
    import pathlib

    offenders = []
    for path in pathlib.Path(".").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if line.lstrip().startswith("#") or "## " in line:
                continue
            if '.get("pnl_usdt", 0)' in line or ".get('pnl_usdt', 0)" in line:
                offenders.append(f"{path.name}:{line_no}")

    assert offenders == [], (
        f"這些地方用了 .get(\"pnl_usdt\", 0):{offenders}。"
        f"欄位存在但值是 None 時預設值不會生效 —— 請用 analytics.pnl_of()。"
    )


# ---------------- Telegram 報告 ----------------

def test_the_telegram_report_and_the_dashboard_read_the_same_numbers():
    """
    舊版自己算一次勝率,而且沒有分離損益基準 —— 於是同一個系統的
    Telegram 勝率與網頁勝率不一樣,看的人不知道該相信哪一個。
    """
    import ast
    import pathlib

    tree = ast.parse(
        pathlib.Path("analytics_report.py").read_text(encoding="utf-8"))

    called = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "get_trade_analytics" in called, "報告要走儀表板同一條路徑"
    assert "get_closed_trades" not in called, (
        "直接讀 trades 表等於自己算一次 —— 那正是兩邊數字分岔的來源"
    )

    # 也不該自己做加總。sum() 出現就代表它在重算。
    assert "sum" not in called, "報告不該自己加總,數字要從 analytics 來"


def test_the_report_says_when_it_dropped_something():
    """一份沒說自己漏了什麼的報告,讀起來跟一份完整的報告一模一樣。"""
    text = analytics_report.build_report({
        "total_trades": 10, "win_rate": 60.0, "total_pnl": 100.0,
        "best": {"pnl_usdt": 50.0}, "worst": {"pnl_usdt": -20.0},
        "unpriced_trades": 3, "pnl_basis": "LEVERAGED", "legacy": {"count": 0},
    })

    assert "3 筆" in text
    assert "損益不明" in text


def test_the_report_flags_legacy_trades_as_not_comparable():
    text = analytics_report.build_report({
        "total_trades": 10, "win_rate": 60.0, "total_pnl": 100.0,
        "best": None, "worst": None,
        "unpriced_trades": 0, "pnl_basis": "LEVERAGED", "legacy": {"count": 5},
    })

    assert "不可與以上數字比較" in text


def test_a_clean_report_says_nothing_extra():
    text = analytics_report.build_report({
        "total_trades": 10, "win_rate": 60.0, "total_pnl": 100.0,
        "best": {"pnl_usdt": 50.0}, "worst": {"pnl_usdt": -20.0},
        "unpriced_trades": 0, "pnl_basis": "LEVERAGED", "legacy": {"count": 0},
    })

    assert "損益不明" not in text
    assert "不可與以上數字比較" not in text


def test_a_missing_best_trade_shows_a_dash_not_zero():
    """「最佳交易 0 USDT」讀起來像一個真的結果。"""
    text = analytics_report.build_report({
        "total_trades": 0, "win_rate": 0, "total_pnl": 0,
        "best": None, "worst": None,
        "unpriced_trades": 0, "pnl_basis": "LEVERAGED", "legacy": {},
    })

    assert "最佳交易:— USDT" in text


def test_the_report_always_states_the_pnl_basis():
    """
    兩種基準的數字不能比較。報告不說自己是哪一種,那個比較就會發生。
    """
    text = analytics_report.build_report({
        "total_trades": 1, "win_rate": 100.0, "total_pnl": 10.0,
        "best": None, "worst": None, "unpriced_trades": 0,
        "pnl_basis": "LEVERAGED", "legacy": {},
    })

    assert "損益基準:LEVERAGED" in text
