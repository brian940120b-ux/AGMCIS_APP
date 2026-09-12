"""
第九十五節:剩下的三個 God Function。

拆開之後,每一條規則都變成可以單獨測的東西。這一份測的就是那些
規則 —— 它們原本埋在一個一百多行的函式中間,只能靠跑整條路徑
間接驗證。

一個共同的紅線:**拆開不能改行為。** 每一組都有一條測試在盯
那件事,而不是只盯新的結構。
"""
from unittest.mock import patch

import pytest

import paper_trading
import risk_control


# ---------------- risk_control 的九項檢查 ----------------

def _snapshot(**overrides):
    """一份全部在安全範圍內的快照。"""
    snapshot = {
        "max_drawdown": 0.0,
        "profit_factor": 2.0,
        "exposure_ratio": 0.0,
        "open_positions": 0,
        "total_open_upnl": 0.0,
        "daily_pnl": 0.0,
        "trades_today": 0,
        "consecutive_losses": 0,
    }
    snapshot.update(overrides)
    return snapshot


def _codes(snapshot):
    return [b.code for b in risk_control.evaluate_limits(snapshot)]


def test_a_clean_snapshot_trips_nothing():
    assert _codes(_snapshot()) == []


def test_each_limit_can_be_tripped_on_its_own():
    """
    原本這九項埋在一個 116 行的函式裡,只能靠跑整條路徑間接驗證。
    """
    import risk_limits

    cases = [
        ("MAX_DRAWDOWN", {"max_drawdown": risk_limits.MAX_DRAWDOWN_PCT}),
        ("MAX_EXPOSURE", {"exposure_ratio": risk_limits.MAX_EXPOSURE_PCT}),
        ("MAX_OPEN_POSITIONS",
         {"open_positions": risk_limits.MAX_OPEN_POSITIONS}),
        ("MAX_TOTAL_OPEN_LOSS",
         {"total_open_upnl": risk_limits.MAX_TOTAL_OPEN_LOSS_USDT}),
        ("MAX_DAILY_LOSS",
         {"daily_pnl": -abs(risk_limits.MAX_DAILY_LOSS_USDT)}),
        ("MAX_CONSECUTIVE_LOSSES",
         {"consecutive_losses": risk_limits.MAX_CONSECUTIVE_LOSSES}),
        ("MAX_TRADES_PER_DAY",
         {"trades_today": risk_limits.MAX_TRADES_PER_DAY}),
    ]

    for code, overrides in cases:
        assert code in _codes(_snapshot(**overrides)), f"{code} 沒有觸發"


def test_emergency_and_blocking_are_different_things():
    """
    持倉數達上限是「今天不要再開了」;回撤超標是「有事情不對勁」。
    把兩者混為一談,會讓一個正常的滿倉狀態看起來像出事了。
    """
    import risk_limits

    full = risk_control.evaluate_limits(
        _snapshot(open_positions=risk_limits.MAX_OPEN_POSITIONS))
    assert full[0].blocks is True
    assert full[0].emergency is False

    drawdown = risk_control.evaluate_limits(
        _snapshot(max_drawdown=risk_limits.MAX_DRAWDOWN_PCT))
    assert drawdown[0].emergency is True


def test_a_low_profit_factor_warns_but_does_not_block():
    """
    用一個回顧性的統計擋住新開倉,會讓一個剛開始跑的系統
    永遠開不了第一筆。
    """
    import risk_limits

    breaches = risk_control.evaluate_limits(
        _snapshot(profit_factor=risk_limits.MIN_PROFIT_FACTOR - 0.5))

    assert [b.code for b in breaches] == ["LOW_PROFIT_FACTOR"]
    assert breaches[0].blocks is False


def test_a_profit_factor_of_zero_means_no_data_not_bad_performance():
    assert _codes(_snapshot(profit_factor=0)) == []


def test_the_daily_loss_limit_accepts_the_setting_written_either_way():
    """設定寫成 50 或 -50 都要當成「虧 50」。"""
    import risk_limits

    limit = abs(risk_limits.MAX_DAILY_LOSS_USDT)

    with patch.object(risk_limits, "MAX_DAILY_LOSS_USDT", limit):
        assert "MAX_DAILY_LOSS" in _codes(_snapshot(daily_pnl=-limit))
    with patch.object(risk_limits, "MAX_DAILY_LOSS_USDT", -limit):
        assert "MAX_DAILY_LOSS" in _codes(_snapshot(daily_pnl=-limit))


def test_a_check_that_raises_counts_as_not_passed(caplog):
    """
    一個在自己壞掉時放行的風控檢查不是風控檢查。
    """
    def broken(_):
        raise RuntimeError("boom")

    breaches = risk_control.evaluate_limits(_snapshot(), checks=(broken,))

    assert len(breaches) == 1
    assert breaches[0].blocks is True
    assert "boom" in breaches[0].message


def test_the_status_dict_still_has_every_field_callers_read():
    """拆開不能改契約。Dashboard、Telegram 與閘門都直接讀這些欄位。"""
    with patch.object(risk_control, "build_snapshot", return_value=_snapshot()), \
         patch("risk_limits.as_dict", return_value={}):
        status = risk_control.get_risk_control_status()

    for key in ("system_status", "allow_new_trade", "emergency_stop",
                "blockers", "max_drawdown", "exposure_ratio",
                "open_positions", "profit_factor", "total_open_upnl",
                "daily_realized_pnl", "trades_last_24h",
                "consecutive_losses", "limits", "alerts"):
        assert key in status, f"少了 {key}"

    assert status["system_status"] == "ACTIVE"
    assert status["allow_new_trade"] is True
    # 沒有任何警示時要有一則「正常」,不是空清單 ——
    # 空清單在畫面上看起來像還沒載入。
    assert status["alerts"][0]["level"] == "NORMAL"


def test_an_emergency_outranks_a_plain_block():
    import risk_limits

    with patch.object(risk_control, "build_snapshot", return_value=_snapshot(
            max_drawdown=risk_limits.MAX_DRAWDOWN_PCT,
            open_positions=risk_limits.MAX_OPEN_POSITIONS)), \
         patch("risk_limits.as_dict", return_value={}):
        status = risk_control.get_risk_control_status()

    assert status["system_status"] == "EMERGENCY_STOP"
    assert status["emergency_stop"] is True


# ---------------- create_paper_trade 的驗證 ----------------

def _valid(**overrides):
    fields = {
        "symbol": "BTC/USDT", "signal": "LONG", "entry_price": 100.0,
        "size_usdt": 200.0, "leverage": 5.0,
        "stoploss": 97.0, "takeprofit": 110.0,
    }
    fields.update(overrides)
    return fields


def test_valid_inputs_pass():
    assert paper_trading._validate_inputs(**_valid()) is None


@pytest.mark.parametrize("overrides,fragment", [
    ({"signal": "SIDEWAYS"}, "方向無法辨識"),
    ({"entry_price": "abc"}, "數值異常"),
    ({"entry_price": 0}, "進場價必須大於 0"),
    ({"size_usdt": 0}, "倉位必須大於 0"),
    ({"leverage": 0}, "槓桿必須大於 0"),
    ({"stoploss": None}, "停損無效"),
    ({"stoploss": 105.0}, "停損無效"),        # 做多但停損在進場價之上
    ({"takeprofit": 90.0}, "停利無效"),        # 做多但停利在進場價之下
])
def test_every_rejection_reason_is_reachable(overrides, fragment):
    """
    這幾條是這個系統最重要的保護。原本它們埋在一個 154 行的函式裡,
    只能靠跑整條開倉路徑間接驗證。
    """
    problem = paper_trading._validate_inputs(**_valid(**overrides))
    assert problem is not None
    assert fragment in problem


def test_a_rejection_says_why():
    """
    回布林值會讓「為什麼不開」消失,而那個理由要進 log 與回傳值
    (第九十四節)。
    """
    problem = paper_trading._validate_inputs(**_valid(stoploss=None))
    assert isinstance(problem, str) and problem


def test_slippage_that_invalidates_the_stop_blocks_the_trade():
    """
    滑價之後停損可能已經在錯邊 —— 那張單一開就會被停掉。
    這一條必須用**成交價**判斷,不能沿用下單價的結論。
    """
    problem = paper_trading._validate_after_costs(
        symbol="BTC/USDT", signal="LONG",
        requested_entry_price=100.0,
        entry_price=96.0,          # 滑價之後已經低於停損
        stoploss=97.0, liquidation_price=None, leverage=5.0,
    )

    assert problem is not None
    assert "滑價後停損失效" in problem


def test_a_liquidation_price_closer_than_the_stop_blocks_the_trade():
    """強平價比停損還近的倉位,實際上根本用不到停損。"""
    problem = paper_trading._validate_after_costs(
        symbol="BTC/USDT", signal="LONG",
        requested_entry_price=100.0, entry_price=100.0,
        stoploss=97.0, liquidation_price=98.0, leverage=20.0,
    )

    assert problem is not None
    assert "強平價" in problem


def test_the_same_check_works_for_shorts():
    problem = paper_trading._validate_after_costs(
        symbol="BTC/USDT", signal="SHORT",
        requested_entry_price=100.0, entry_price=100.0,
        stoploss=103.0, liquidation_price=102.0, leverage=20.0,
    )

    assert problem is not None
    assert "強平價" in problem


def test_an_unknown_liquidation_price_does_not_block():
    """
    算不出強平價時不擋 —— 那一項的**其他保護仍然在**
    (停損有效、槓桿上限、風控閘門)。用「算不出來」當成拒絕理由,
    會讓一個沒有校準規格的環境完全開不了倉。
    """
    assert paper_trading._validate_after_costs(
        symbol="BTC/USDT", signal="LONG",
        requested_entry_price=100.0, entry_price=100.0,
        stoploss=97.0, liquidation_price=None, leverage=5.0,
    ) is None


def test_costs_are_applied_before_the_second_round_of_checks():
    """
    順序是這個函式最容易改壞的地方:成本後驗證必須看到成交價,
    不是下單價。

    用 AST 讀**實際的呼叫順序**,不是掃字串 —— docstring 裡提到
    函式名稱是在解釋設計,那不是一次呼叫。
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(
        inspect.getsource(paper_trading.create_paper_trade)))

    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in (
            "_validate_inputs", "_apply_costs", "_validate_after_costs",
        )
    ]

    # ast.walk 不保證順序,所以依行號排。
    ordered = sorted(
        (node.lineno, node.func.id)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in (
            "_validate_inputs", "_apply_costs", "_validate_after_costs",
        )
    )

    assert [name for _, name in ordered] == [
        "_validate_inputs", "_apply_costs", "_validate_after_costs",
    ]
    assert len(calls) == 3


# ---------------- 舊的第二套評分已經移除 ----------------

def test_the_old_second_scorer_is_gone():
    """
    一份「沒有人用但還能跑」的評分公式是最危險的死碼:
    它看起來可以拿來用,而它的答案跟活著的那條管線不一樣。
    """
    import strategy

    with pytest.raises(NotImplementedError) as caught:
        strategy.analyze_symbol("BTC/USDT", None)

    assert "agmcis.signal.pipeline" in str(caught.value)


def test_removing_it_does_not_silently_forward_to_the_new_pipeline():
    """
    兩者的回傳型別不同(dict vs Signal)。安靜地轉過去,呼叫端會拿到
    一個結構不同的東西,然後在別的地方壞掉。

    檢查的是**函式主體只有一個 raise**,不是掃字串 ——
    docstring 裡寫出新的入口是在指路,那正是它該做的事。
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path("strategy.py").read_text(encoding="utf-8"))
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "analyze_symbol"
    ]

    assert len(functions) == 1

    body = [node for node in functions[0].body
            if not isinstance(node, ast.Expr)]      # 去掉 docstring
    assert len(body) == 1
    assert isinstance(body[0], ast.Raise)

    # 而且這個模組不再 import 任何指標套件 —— 那 132 行真的走了。
    imports = [n for n in ast.walk(tree)
               if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert imports == []


# ---------------- 不要再長出新的 God Function ----------------

MAX_STATEMENTS = 45

# 這兩個刻意超標,而且**不該拆**。
#
# 它們是單一資料庫交易:讀部位 → 算損益 → 更新交易 → 更新帳戶,
# 全部在同一個 transaction 裡。拆成幾個函式的話,中間任何一步失敗
# 都會留下一筆改了一半的紀錄 —— 而那是這個系統最不能接受的狀態。
# 原子性比行數重要。
ALLOWED_LONG = {
    "database_service.py:reduce_trade_atomic":
        "單一資料庫交易,拆開會破壞原子性",
    "database_service.py:close_trade_atomic":
        "單一資料庫交易,拆開會破壞原子性",
}


def _statement_count(node):
    """
    敘述數,不是行數。

    這個 codebase 的註解密度很高,行數會把一個寫得很清楚的函式
    算成 God Function。敘述數量比較接近「這個函式在做幾件事」。
    docstring 與純字串運算式不算。
    """
    import ast

    count = 0
    for child in ast.walk(node):
        if not isinstance(child, ast.stmt) or child is node:
            continue
        if (isinstance(child, ast.Expr)
                and isinstance(child.value, ast.Constant)
                and isinstance(child.value.value, str)):
            continue
        count += 1
    return count


def test_no_new_god_functions_in_the_root_modules():
    """
    第九十五節列的 God Function。這一條擋的是**回歸** ——
    拆完之後如果沒有守著,下一次加功能又會長回去。

    要新增例外,加進 ALLOWED_LONG 並寫下理由。寫不出理由的,
    就是該拆的那一個。
    """
    import ast
    import pathlib

    offenders = []

    for path in sorted(pathlib.Path(".").glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            key = f"{path.name}:{node.name}"
            if key in ALLOWED_LONG:
                continue

            count = _statement_count(node)
            if count > MAX_STATEMENTS:
                offenders.append(f"{key} 有 {count} 個敘述")

    assert offenders == [], (
        "這些函式超過 " + str(MAX_STATEMENTS) + " 個敘述:"
        + "、".join(offenders)
        + "。拆開它,或加進 ALLOWED_LONG 並寫下不拆的理由。"
    )


def test_every_allowed_exception_still_exists():
    """
    例外清單裡的函式被改名或刪掉時,那一筆會靜靜地永遠成立。
    """
    import ast
    import pathlib

    for key in ALLOWED_LONG:
        filename, function = key.split(":")
        tree = ast.parse(
            pathlib.Path(filename).read_text(encoding="utf-8"))

        names = {
            node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert function in names, f"{key} 已經不存在,把它從 ALLOWED_LONG 移除"


def test_the_allowed_exceptions_are_actually_atomic():
    """
    它們的豁免理由是「單一資料庫交易」。如果哪天它們不再用
    transaction,那個理由就不成立了 —— 而豁免會留著。
    """
    import inspect

    import database_service

    for key in ALLOWED_LONG:
        function = getattr(database_service, key.split(":")[1])
        source = inspect.getsource(function)
        assert "transaction()" in source, f"{key} 已經不是單一交易了"
