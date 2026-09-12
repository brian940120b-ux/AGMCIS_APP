"""
第八十七 / 六十三 / 六十一節:開倉前看得到的東西。

三個端點,一個共同的紅線:**它們不下單、不寫決策紀錄。**
一個預覽如果會產生副作用,它就不是預覽了。
"""
import logging
from unittest.mock import patch

from agmcis.core.enums import Direction, MarketType
from agmcis.core.models import RiskDecision, TradeIntent
import api.performance_dashboard as perf
import api.trade_panel as panel


# ---------------- 測試替身 ----------------

class FakeOpinion:
    def __init__(self, agent, vote, confidence=50.0, reasons=(), error=None):
        self.agent = agent
        self.vote = type("V", (), {"value": vote})()
        self.confidence = confidence
        self.reasons = list(reasons)
        self.error = error


class FakeDeliberation:
    def __init__(self, votes, intent=None, opinions=None,
                 confidence=72.0, direction="LONG"):
        self.votes = dict(votes)
        self.intent = intent
        self.opinions = opinions or [
            FakeOpinion(name, vote) for name, vote in votes.items()
        ]
        self.confidence = confidence
        self.direction = type("D", (), {"value": direction})()
        self.blocked_reason = None


class FakeReport:
    def __init__(self, vetoed=False, veto_reasons=(), errored=(), warnings=()):
        self._payload = {
            "total_agents": 12,
            "errored": list(errored),
            "abstained": [],
            "vetoed": vetoed,
            "veto_reasons": list(veto_reasons),
            "health_warnings": list(warnings),
        }

    def to_dict(self):
        return dict(self._payload)


def an_intent(symbol="BTC/USDT"):
    return TradeIntent(
        symbol=symbol, market_type=MarketType.PERPETUAL,
        direction=Direction.LONG, entry=100.0,
        stop_loss=97.0, take_profit=109.0,
        confidence=72.0, strategy="trend_following",
    )


def approved(intent):
    return RiskDecision(
        intent=intent, approved=True, size_usdt=200.0, leverage=5.0,
        reason="通過", warnings=[],
    )


def rejected(intent, blockers=("DAILY_LOSS_LIMIT",)):
    return RiskDecision(
        intent=intent, approved=False, reason="日虧損上限",
        blockers=list(blockers),
    )


# ---------------- 第八十七節:開倉預覽 ----------------

def test_approved_plan_carries_every_field_section_87_lists():
    intent = an_intent()
    deliberation = FakeDeliberation({"trend": "LONG"}, intent=intent)

    with patch("auto_trader._agent_intent",
               return_value=(intent, None, deliberation, FakeReport())), \
         patch("risk_control.evaluate_intent", return_value=approved(intent)), \
         patch.object(panel, "_risk_pct", return_value=1.0):
        plan = panel.build_plan("BTC/USDT")

    for key in ("symbol", "market", "direction", "entry", "stop_loss",
                "take_profit", "leverage", "position_size_usdt", "risk_usdt",
                "risk_reward", "confidence", "score"):
        assert key in plan, f"第八十七節列的 {key} 不見了"

    assert plan["stage"] == "APPROVED"
    assert plan["tradable"] is True
    assert plan["leverage"] == 5.0
    assert plan["notional_usdt"] == 1000.0
    assert plan["risk_reward"] == 3.0


def test_preview_never_marks_itself_submitted():
    intent = an_intent()
    deliberation = FakeDeliberation({"trend": "LONG"}, intent=intent)

    with patch("auto_trader._agent_intent",
               return_value=(intent, None, deliberation, FakeReport())), \
         patch("risk_control.evaluate_intent", return_value=approved(intent)), \
         patch.object(panel, "_risk_pct", return_value=1.0):
        plan = panel.build_plan("BTC/USDT")

    assert plan["submitted"] is False


def test_preview_does_not_place_an_order():
    """
    最重要的一條。預覽走到 Risk Engine 就停 ——
    Execution Engine 不該被碰到。
    """
    intent = an_intent()
    deliberation = FakeDeliberation({"trend": "LONG"}, intent=intent)

    with patch("auto_trader._agent_intent",
               return_value=(intent, None, deliberation, FakeReport())), \
         patch("risk_control.evaluate_intent", return_value=approved(intent)), \
         patch.object(panel, "_risk_pct", return_value=1.0), \
         patch("agmcis.execution.engine.get_engine") as engine:
        panel.build_plan("BTC/USDT")

    engine.assert_not_called()


def test_preview_does_not_write_a_decision_record():
    """
    看一眼不是打算。把每次預覽寫進 ai_decisions,那張表會被
    「使用者看了但系統從沒打算做」的紀錄灌滿。
    """
    intent = an_intent()
    deliberation = FakeDeliberation({"trend": "LONG"}, intent=intent)

    with patch("auto_trader._agent_intent",
               return_value=(intent, None, deliberation, FakeReport())), \
         patch("risk_control.evaluate_intent", return_value=approved(intent)), \
         patch.object(panel, "_risk_pct", return_value=1.0), \
         patch("agmcis.review.decision_log.record") as record:
        panel.build_plan("BTC/USDT")

    record.assert_not_called()


def test_rejected_plan_still_shows_entry_and_stop():
    """
    Agent 的結論成立與否,跟風控放不放行是兩件事。
    只有倉位相關的數字沒有意義。
    """
    intent = an_intent()
    deliberation = FakeDeliberation({"trend": "LONG"}, intent=intent)

    with patch("auto_trader._agent_intent",
               return_value=(intent, None, deliberation, FakeReport())), \
         patch("risk_control.evaluate_intent", return_value=rejected(intent)):
        plan = panel.build_plan("BTC/USDT")

    assert plan["stage"] == "REJECTED_BY_RISK"
    assert plan["tradable"] is False
    assert plan["entry"] == 100.0
    assert plan["stop_loss"] == 97.0
    assert plan["leverage"] is None
    assert plan["position_size_usdt"] is None
    assert plan["blockers"] == ["DAILY_LOSS_LIMIT"]


def test_missing_numbers_are_none_not_zero():
    """
    「Position Size 0」看起來是系統算出來的結論,而實際上是沒算。
    """
    plan = panel._empty("BTC/USDT", "NO_INTENT", "全體棄權")

    for key in ("entry", "stop_loss", "leverage", "position_size_usdt",
                "risk_usdt", "risk_reward", "confidence", "score"):
        assert plan[key] is None, f"{key} 應該是 None 而不是 {plan[key]!r}"


def test_no_intent_is_not_an_error():
    """觀望是合法結論(第二十九節),不是失敗。"""
    deliberation = FakeDeliberation({"trend": "WAIT", "volume": "ABSTAIN"})

    with patch("auto_trader._agent_intent",
               return_value=(None, "沒有足夠共識", deliberation, FakeReport())):
        plan = panel.build_plan("BTC/USDT")

    assert plan["stage"] == "NO_INTENT"
    assert plan["reason"] == "沒有足夠共識"
    assert plan["votes"] == {"trend": "WAIT", "volume": "ABSTAIN"}


def test_pipeline_failure_becomes_an_error_plan_not_an_exception(caplog):
    with patch("auto_trader._agent_intent", side_effect=RuntimeError("boom")), \
         caplog.at_level(logging.ERROR):
        plan = panel.build_plan("BTC/USDT")

    assert plan["stage"] == "ERROR"
    assert "boom" in plan["reason"]
    assert "BTC/USDT" in caplog.text


def test_score_is_none_because_the_agent_path_has_no_score():
    """
    第八十七節列了 Score,但 Agent 共識管線不產生分數。
    填 confidence 冒充它,會讓一個沒有分數的系統看起來有兩個獨立指標。
    """
    intent = an_intent()
    assert intent.score is None

    deliberation = FakeDeliberation({"trend": "LONG"}, intent=intent)

    with patch("auto_trader._agent_intent",
               return_value=(intent, None, deliberation, FakeReport())), \
         patch("risk_control.evaluate_intent", return_value=approved(intent)), \
         patch.object(panel, "_risk_pct", return_value=1.0):
        plan = panel.build_plan("BTC/USDT")

    assert plan["score"] is None
    assert plan["confidence"] == 72.0


def test_risk_pct_is_none_when_equity_is_unavailable(caplog):
    intent = an_intent()
    decision = approved(intent)

    with patch("agmcis.risk.account_state.build_account_state",
               side_effect=RuntimeError("db down")), \
         caplog.at_level(logging.WARNING):
        assert panel._risk_pct(decision) is None


def test_risk_pct_is_none_when_equity_is_zero():
    """除以零會拋,回 0 會讓一筆風險看起來是零。兩個都不對。"""
    intent = an_intent()
    decision = approved(intent)

    state = type("S", (), {"equity": 0.0})()
    with patch("agmcis.risk.account_state.build_account_state", return_value=state):
        assert panel._risk_pct(decision) is None


# ---------------- 第六十三節:決策鏈 ----------------

def _flow(plan):
    return {s["stage"]: s for s in panel._stages(plan)}


def test_flow_covers_the_whole_chain_in_order():
    plan = panel._empty("BTC/USDT", "NO_INTENT", "沒有共識")
    names = [s["stage"] for s in panel._stages(plan)]
    assert names == list(panel.STAGE_ORDER)


def test_execution_stage_is_never_green_in_a_preview():
    """
    一張把最後一格畫成綠色的流程圖,會讓人以為單已經送出去了。
    """
    plan = {
        "stage": "APPROVED", "votes": {"trend": "LONG"},
        "supervisor": {"vetoed": False}, "leverage": 5, "confidence": 72,
        "position_size_usdt": 200, "direction": "LONG",
    }
    flow = _flow(plan)
    assert flow["risk"]["status"] == panel.PASSED
    assert flow["execution"]["status"] == panel.NOT_RUN


def test_stages_after_a_block_are_not_reached_not_passed():
    plan = {
        "stage": "NO_INTENT", "votes": {"trend": "WAIT"},
        "supervisor": {"vetoed": False}, "reason": "沒有共識",
    }
    flow = _flow(plan)

    assert flow["agents"]["status"] == panel.PASSED
    assert flow["consensus"]["status"] == panel.BLOCKED
    assert flow["risk"]["status"] == panel.NOT_REACHED


def test_a_supervisor_veto_shows_at_the_supervisor_stage():
    plan = {
        "stage": "NO_INTENT", "votes": {"trend": "LONG"},
        "supervisor": {"vetoed": True, "veto_reasons": ["半數 Agent 失敗"]},
        "reason": "監督者否決",
    }
    flow = _flow(plan)

    assert flow["supervisor"]["status"] == panel.BLOCKED
    assert "半數 Agent 失敗" in flow["supervisor"]["detail"]


def test_all_agents_failing_is_an_error_stage():
    plan = {
        "stage": "NO_INTENT", "votes": {"a": "ABSTAIN", "b": "ABSTAIN"},
        "supervisor": {"errored": ["a", "b"]},
        "reason": "全部失敗",
    }
    flow = _flow(plan)
    assert flow["agents"]["status"] == panel.ERROR
    assert flow["consensus"]["status"] == panel.NOT_REACHED


def test_risk_block_lists_the_blockers_on_the_risk_stage():
    plan = {
        "stage": "REJECTED_BY_RISK", "votes": {"trend": "LONG"},
        "supervisor": {"vetoed": False}, "reason": "日虧損上限",
        "blockers": ["DAILY_LOSS_LIMIT"], "direction": "LONG", "confidence": 72,
    }
    flow = _flow(plan)

    assert flow["consensus"]["status"] == panel.PASSED
    assert flow["risk"]["status"] == panel.BLOCKED
    assert flow["risk"]["blockers"] == ["DAILY_LOSS_LIMIT"]


def test_flow_reuses_the_same_plan_it_shows():
    """
    流程圖顯示的必須是真的跑過的那一輪。跑兩次的結果可以不同,
    而一張跟決策對不起來的流程圖沒有用。
    """
    with patch.object(panel, "build_plan", return_value=panel._empty(
            "BTC/USDT", "NO_INTENT", "x")) as build:
        result = panel.build_flow("BTC/USDT")

    assert build.call_count == 1
    assert result["plan"]["symbol"] == "BTC/USDT"


# ---------------- 第六十一節:績效 ----------------

def _closed(pnl, strategy="a"):
    return {"status": "CLOSED", "pnl_usdt": pnl, "strategy": strategy}


def test_best_strategy_needs_enough_trades():
    """
    一個兩戰兩勝的策略排在第一名,是在鼓勵人去跟一個不存在的優勢。
    """
    trades = [_closed(10, "lucky"), _closed(10, "lucky")]
    result = perf._strategy(trades)

    assert result["best"] is None
    assert result["worst"] is None
    assert result["insufficient_sample"] == ["lucky"]


def test_best_and_worst_are_picked_once_the_sample_is_large_enough():
    from agmcis.review.attribution import MIN_SAMPLE

    trades = (
        [_closed(10, "good")] * MIN_SAMPLE
        + [_closed(-10, "bad")] * MIN_SAMPLE
    )
    result = perf._strategy(trades)

    assert result["best"]["key"] == "good"
    assert result["worst"]["key"] == "bad"


def test_strategies_are_ranked_by_expectancy_not_total_pnl():
    """
    總損益偏袒交易次數多的策略,而那不是「比較好」,是「跑得比較多」。
    """
    from agmcis.review.attribution import MIN_SAMPLE

    # few 每筆賺 10,many 每筆賺 1 但筆數是三倍 —— 總損益 many 較高。
    trades = (
        [_closed(10, "few")] * MIN_SAMPLE
        + [_closed(1, "many")] * (MIN_SAMPLE * 3)
    )
    result = perf._strategy(trades)

    assert result["best"]["key"] == "few"


def test_trades_without_a_strategy_name_are_counted_not_bucketed():
    """
    加總對不起來會讓人以為統計算錯,所以被排除的筆數要顯示。
    """
    trades = [_closed(5, "a"), _closed(5, None), _closed(5, None)]
    result = perf._strategy(trades)

    assert result["unclassified"] == 2
    assert [b["key"] for b in result["strategies"]] == ["a"]


def test_profit_factor_is_none_when_there_are_no_losses():
    """無限大排序起來永遠第一。"""
    result = perf._trading([_closed(10), _closed(20)])
    assert result["profit_factor"] is None


def test_trading_block_says_when_the_sample_is_too_small():
    result = perf._trading([_closed(1)])
    assert result["reliable"] is False


def test_each_block_degrades_on_its_own(caplog):
    """一塊壞掉不該讓整個面板變成錯誤頁。"""
    with patch("agmcis.risk.account_state.build_account_state",
               side_effect=RuntimeError("db down")), \
         patch.object(perf, "_closed_trades", return_value=[_closed(5)]), \
         caplog.at_level(logging.ERROR):
        dashboard = perf.build_dashboard()

    assert "error" in dashboard["account"]
    assert "error" in dashboard["risk"]
    # 不依賴帳戶的那兩塊照樣算得出來
    assert dashboard["trading"]["trades"] == 1
    assert "error" not in dashboard["strategy"]


def test_dashboard_has_the_four_blocks_section_61_lists():
    with patch.object(perf, "_closed_trades", return_value=[]), \
         patch("agmcis.risk.account_state.build_account_state",
               side_effect=RuntimeError("offline")):
        dashboard = perf.build_dashboard()

    assert set(dashboard) == {"account", "trading", "risk", "strategy"}


# ---------------- 路由 ----------------

def test_all_three_endpoints_are_get_only():
    """
    預覽、流程圖、績效都是查詢。任何一個變成 POST 都代表它有副作用。
    """
    import main

    spec = main.app.openapi()
    for path in ("/api/trade_plan", "/api/trade_plans",
                 "/api/agent_flow", "/api/performance_dashboard"):
        assert path in spec["paths"], f"{path} 沒有註冊"
        assert set(spec["paths"][path]) == {"get"}, f"{path} 不該有 GET 以外的方法"


def test_the_trading_page_route_exists():
    import main

    paths = {getattr(r, "path", None) for r in main.app.routes}
    assert "/trading" in paths


def test_the_panel_module_never_imports_the_execution_engine():
    """
    靜態保證:這個模組的原始碼裡不該出現送單的呼叫。
    測試會忘記涵蓋新增的分支,但這一條會擋住整類錯誤。
    """
    from pathlib import Path

    source = Path("api/trade_panel.py").read_text(encoding="utf-8")
    for forbidden in ("execution.get_engine", ".execute(", "place_order"):
        assert forbidden not in source, f"交易面板不該碰 {forbidden}"


# ---------------- 第五十三節:首頁 TOP 機會的門檻 ----------------
#
# 這一組是為了一個真的存在過的錯誤:首頁拿 `score` 去比對
# MIN_SIGNAL_SCORE,但 Agent 共識管線**不產生 score** ——
# 那個欄位永遠是 None,所以合格清單永遠是空的,而畫面上顯示的是
# 「NO HIGH QUALITY SETUP」。那個畫面跟「系統壞了」長得一樣。

class FakeIntentHolder:
    """有 intent 就代表共識層放行了。"""


def _overview_deliberation(confidence, tradable):
    deliberation = FakeDeliberation(
        {"trend": "LONG"}, confidence=confidence,
        intent=FakeIntentHolder() if tradable else None,
    )
    return deliberation


def test_a_tradable_consensus_is_a_qualified_opportunity():
    from api import overview

    with patch("agmcis.signal.agent_pipeline.analyse_symbol",
               return_value=(_overview_deliberation(80.0, True), FakeReport())), \
         patch.object(overview.settings, "WATCHLIST_SYMBOLS", ["BTC/USDT"]):
        result = overview._opportunities(limit=1)

    assert result["no_high_quality_setup"] is False
    assert [row["symbol"] for row in result["top"]] == ["BTC/USDT"]


def test_no_tradable_consensus_means_no_high_quality_setup():
    from api import overview

    with patch("agmcis.signal.agent_pipeline.analyse_symbol",
               return_value=(_overview_deliberation(80.0, False), FakeReport())), \
         patch.object(overview.settings, "WATCHLIST_SYMBOLS", ["BTC/USDT"]):
        result = overview._opportunities(limit=1)

    assert result["no_high_quality_setup"] is True
    # 掃過的仍然列出來 —— 藏起來會讓人以為系統沒在跑。
    assert len(result["considered"]) == 1


def test_opportunities_rank_by_confidence():
    from api import overview

    def analyse(symbol, **kwargs):
        confidence = {"A/USDT": 60.0, "B/USDT": 90.0}[symbol]
        return _overview_deliberation(confidence, True), FakeReport()

    with patch("agmcis.signal.agent_pipeline.analyse_symbol", side_effect=analyse), \
         patch.object(overview.settings, "WATCHLIST_SYMBOLS", ["A/USDT", "B/USDT"]):
        result = overview._opportunities(limit=2)

    assert [row["symbol"] for row in result["considered"]] == ["B/USDT", "A/USDT"]


def test_the_extra_confidence_threshold_defaults_to_off():
    """
    共識層自己已經有一道門檻。在這裡再設一個預設值等於偷偷收緊
    出手條件,而且那個收緊不會出現在任何風控紀錄裡。
    """
    from agmcis.config import settings

    assert settings.MIN_AGENT_CONFIDENCE == 0.0


def test_the_extra_confidence_threshold_filters_when_set():
    from api import overview

    with patch("agmcis.signal.agent_pipeline.analyse_symbol",
               return_value=(_overview_deliberation(40.0, True), FakeReport())), \
         patch.object(overview.settings, "WATCHLIST_SYMBOLS", ["BTC/USDT"]):
        result = overview._opportunities(limit=1, min_confidence=70)

    assert result["no_high_quality_setup"] is True


def test_active_agent_count_excludes_abstentions():
    """
    一個永遠顯示 12/12 的儀表板,在三個 Agent 棄權時看起來一模一樣。
    """
    from api import overview

    deliberation = FakeDeliberation(
        {"trend": "LONG", "volume": "ABSTAIN", "news": "WAIT"},
        intent=FakeIntentHolder(),
    )

    with patch("agmcis.signal.agent_pipeline.analyse_symbol",
               return_value=(deliberation, FakeReport())), \
         patch.object(overview.settings, "WATCHLIST_SYMBOLS", ["BTC/USDT"]):
        result = overview._opportunities(limit=1)

    assert result["active_agents"] == 2


def test_the_home_page_no_longer_reads_a_score_field():
    """
    前端也要一起改。留著 `entry.score` 會在畫面上顯示一個永遠是 0 的
    「分數」欄位 —— 比拿掉更糟,因為 0 讀起來像一個真的分數。
    """
    from pathlib import Path

    source = Path("static/js/home.js").read_text(encoding="utf-8")
    assert "entry.score" not in source
    assert "data.min_score" not in source
