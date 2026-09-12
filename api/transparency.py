"""
透明度端點。

Phase 9 到 13 加了很多東西:Agent 投票、訂單狀態機、對帳差異、成本統計、
規格校準狀態。到目前為止這些**只在 log 裡**。

看不到就等於不存在。一個會在背景默默把裸倉平掉、默默發現對帳差異的系統,
如果沒有人看得到它做了什麼,那些機制的價值會被打掉一大半 ——
而且你不會知道它是「沒有問題」還是「壞掉了所以什麼都沒回報」。

這些端點全部是**唯讀**的。沒有一個會下單、平倉或改設定。
"""
import logging

from fastapi import APIRouter, Query

from agmcis.config import settings

router = APIRouter()
logger = logging.getLogger("agmcis.api.transparency")

MAX_ORDERS = 100


def _safe(name, fn, fallback):
    """
    單一區塊壞掉不該讓整個頁面空白,但**必須說出來** ——
    回傳 fallback 加上 error,而不是假裝那一塊是空的。
    """
    try:
        return fn()
    except Exception as exc:
        logger.exception("透明度端點 %s 失敗", name)
        result = dict(fallback)
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result


# 路由函式只負責解參數,實際邏輯都在 _xxx() ——
# 這樣它們在 FastAPI 之外也叫得動(給摘要端點與測試用)。
# 直接呼叫帶 Query() 預設值的路由函式,拿到的會是 Query 物件而不是數字。


@router.get("/api/agent_votes")
def api_agent_votes(symbol: str = Query(None), limit: int = Query(5, ge=1, le=20)):
    return _agent_votes(symbol=symbol, limit=limit)


def _agent_votes(symbol=None, limit=5):
    """
    Agent 群現在怎麼看這些標的。

    這是 Dashboard 上最重要的一塊:它回答「系統為什麼沒有交易」。
    大多數時候答案是某個環境型 Agent 投了 WAIT,而那在 log 裡要翻很久。
    """
    def run():
        from agmcis.signal import agent_pipeline

        symbols = [symbol] if symbol else list(settings.WATCHLIST_SYMBOLS)[:limit]
        rows = []

        for name in symbols:
            deliberation, report = agent_pipeline.analyse_symbol(name)

            rows.append({
                "symbol": name,
                "direction": deliberation.direction.value,
                "confidence": round(deliberation.confidence, 2),
                "has_intent": deliberation.intent is not None,
                "blocked_reason": deliberation.blocked_reason,
                "votes": dict(deliberation.votes),
                "reasons": list(deliberation.reasons)[:5],
                "errors": list(deliberation.errors),
                "supervisor": report.to_dict() if report else None,
            })

        return {"symbols": rows}

    return _safe("agent_votes", run, {"symbols": []})


@router.get("/api/orders")
def api_orders(limit: int = Query(50, ge=1, le=MAX_ORDERS)):
    return _orders(limit=limit)


def _orders(limit=50):
    """
    訂單狀態機現在的樣子。

    特別要看的是 `needs_reconciliation` 那幾個 —— 狀態不明的訂單
    代表系統不知道交易所收到了什麼,而那些單不可以重送。
    """
    def run():
        from agmcis.core.enums import OrderState
        from agmcis.execution import order_store

        store = order_store.get_store()
        unresolved = store.unresolved()
        open_orders = store.open_orders()

        def row(order):
            return {
                "client_order_id": order.client_order_id,
                "exchange_order_id": order.exchange_order_id,
                "symbol": order.symbol,
                "side": order.side.value,
                "state": order.state.value,
                "quantity": order.quantity,
                "filled_quantity": order.filled_quantity,
                "average_fill_price": order.average_fill_price,
                "reject_reason": order.reject_reason,
                "needs_reconciliation": order.state.needs_reconciliation,
                "is_naked": order.state in (
                    OrderState.FILLED, OrderState.PARTIALLY_FILLED,
                ),
            }

        return {
            "unresolved": [row(o) for o in unresolved[:limit]],
            "open": [row(o) for o in open_orders[:limit]],
            "unresolved_count": len(unresolved),
            "open_count": len(open_orders),
            # 已成交但沒有 PROTECTED = 裸倉。這個數字不該大於 0。
            "naked_count": len([
                o for o in open_orders
                if o.state in (OrderState.FILLED, OrderState.PARTIALLY_FILLED)
            ]),
        }

    return _safe("orders", run, {"unresolved": [], "open": []})


@router.get("/api/reconciliation")
def api_reconciliation():
    return _reconciliation()


def _reconciliation():
    """
    跑一次對帳並回傳結果。

    對帳本身是唯讀的 —— 它只更正本地紀錄、回報差異,絕不下單。
    所以從 API 觸發是安全的。
    """
    def run():
        from agmcis.execution.reconciliation import run_reconciliation

        return run_reconciliation()

    return _safe("reconciliation", run, {"ok": None, "discrepancies": []})


@router.get("/api/costs")
def api_costs():
    return _costs()


def _costs():
    """
    成本統計。回測說有優勢的策略,扣掉成本之後還剩多少。

    只統計 cost_basis = WITH_COSTS 的交易 —— Phase 10 之前的資料
    完全不含成本,混在一起平均會讓這個問題永遠問不出答案。
    """
    def run():
        from agmcis.execution import paper_costs
        from paper_trading import get_paper_summary

        summary = get_paper_summary()
        model = paper_costs.get_cost_model()

        return {
            "costs": summary.get("costs", {}),
            "balance": summary.get("balance"),
            "model": {
                "maker_fee": model.maker_fee,
                "taker_fee": model.taker_fee,
                "slippage_pct": model.slippage_pct,
                "spread_pct": model.spread_pct,
                "funding_rate_8h": model.funding_rate_8h,
                "liquidation_fee": model.liquidation_fee,
                "round_trip_cost_pct": round(model.round_trip_cost_pct(), 6),
            },
        }

    return _safe("costs", run, {"costs": {}, "model": {}})


@router.get("/api/calibration")
def api_calibration():
    return _calibration()


def _calibration():
    """
    有多少數字是交易所給的,有多少是猜的。

    全部都是 DEFAULT_GUESS 的系統,它算出來的強平價與成本都只是估計。
    上線前必看。
    """
    def run():
        from agmcis.exchange import specs

        return specs.get_store().calibration_report(settings.WATCHLIST_SYMBOLS)

    return _safe("calibration", run, {"calibrated": False, "symbols": {}})


@router.get("/api/self_review")
def api_self_review():
    return _self_review()


def _self_review():
    """
    自我檢討:哪些東西其實沒有貢獻。

    這份報告的設計目標是**能說出「我不知道」和「這裡在虧錢」**。
    一份只會說好消息的檢討報告比沒有檢討報告更糟。
    """
    def run():
        from agmcis.review.self_review import run_self_review

        return run_self_review()

    return _safe("self_review", run, {"verdict": "NOT_ENOUGH_DATA", "findings": []})


@router.get("/api/config_changes")
def api_config_changes(limit: int = Query(20, ge=1, le=200)):
    return _config_changes(limit=limit)


def _config_changes(limit=20):
    """
    風控參數的變更紀錄。

    這一層答得出「什麼時候變的、從多少變成多少」,**答不出「是誰改的」**——
    設定來自環境變數,環境變數沒有作者。不假裝做得到。
    """
    def run():
        from agmcis.config.audit import get_auditor

        auditor = get_auditor()
        records = auditor.read_audit(limit=limit)

        risk_increases = [
            change
            for record in records
            for change in record.get("changes", [])
            if change.get("kind") == "RISK_INCREASED"
        ]

        return {
            "records": records,
            "risk_increase_count": len(risk_increases),
            "recent_risk_increases": risk_increases[-5:],
        }

    return _safe("config_changes", run, {"records": []})


@router.get("/api/strategy_health")
def api_strategy_health():
    return _strategy_health()


def _strategy_health():
    """
    每個策略現在的生命週期狀態與健康度(第七十三 ~ 七十六節)。

    這一塊回答的是「為什麼這個策略最近都沒出手」——
    答案可能是它被回撤上限自動停掉了,而那件事只寫在 log 裡。

    **唯讀。** 它只評估,不會改狀態 —— 改狀態是排程的 drift_monitor 的事。
    """
    def run():
        from agmcis.strategy.health import evaluate_all, get_store
        from agmcis.strategy.registry import get_registry

        from database_service import get_closed_trades

        trades = get_closed_trades()
        registry = get_registry()
        store = get_store()

        reports = evaluate_all(trades, names=registry.names)
        enabled, disabled = registry.active()

        return {
            "strategies": [r.to_dict() for r in reports],
            "enabled": [s.name for s in enabled],
            "disabled": disabled,
            "paused_count": sum(
                1 for r in reports if r.status == "paused"
            ),
            "drifted_count": sum(
                1 for r in reports if r.verdict == "STRATEGY_DRIFT"
            ),
            "recent_changes": store.read_audit(limit=20),
        }

    return _safe("strategy_health", run, {
        "strategies": [], "enabled": [], "disabled": [],
        "paused_count": 0, "drifted_count": 0, "recent_changes": [],
    })


@router.get("/api/decisions")
def api_decisions(limit: int = Query(30, ge=1, le=200),
                  symbol: str = Query(None),
                  outcome: str = Query(None)):
    return _decisions(limit=limit, symbol=symbol, outcome=outcome)


def _decisions(limit=30, symbol=None, outcome=None):
    """
    決策紀錄(第七十節)。**包含沒有下單的決策。**

    系統連續三天沒有交易的時候,唯一能回答「它是壞了還是在等」的
    就是這批紀錄 —— 而那個問題只看訂單表永遠答不出來。
    """
    def run():
        from database_service import get_decisions

        rows = get_decisions(limit=limit, symbol=symbol, outcome=outcome)
        counts = {}
        for row in rows:
            counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1

        return {
            "decisions": rows,
            "count": len(rows),
            "by_outcome": counts,
        }

    return _safe("decisions", run, {"decisions": [], "count": 0, "by_outcome": {}})


@router.get("/api/why/{trade_id}")
def api_why(trade_id: int):
    return _why(trade_id)


def _why(trade_id):
    """
    「為什麼你開這一單?」(第七十一節)

    可解釋性不是「當下說得出來」,是**事後查得到**。這個端點回答的
    是後者 —— 而且刻意不隱藏缺漏:沒有存到的項目會寫「沒有紀錄」,
    不會被省略掉。省略會讓讀的人以為那一項沒有意見。
    """
    def run():
        from agmcis.review.decision_log import explain_trade
        return explain_trade(trade_id).to_dict()

    return _safe("why", run, {"found": False, "lines": []})


@router.get("/api/risk_events")
def api_risk_events(limit: int = Query(30, ge=1, le=200)):
    return _risk_events(limit=limit)


def _risk_events(limit=30):
    """風控事件。被擋下來的、被縮小的、觸發緊急保護的。"""
    def run():
        from database_service import get_risk_events
        events = get_risk_events(limit=limit)
        return {
            "events": events,
            "critical_count": sum(
                1 for e in events if e.get("severity") == "CRITICAL"
            ),
        }

    return _safe("risk_events", run, {"events": [], "critical_count": 0})


@router.get("/api/audit_logs")
def api_audit_logs(limit: int = Query(30, ge=1, le=200)):
    return _audit_logs(limit=limit)


def _audit_logs(limit=30):
    """系統稽核(第六十五節):登入、模式切換、策略狀態變更、緊急操作。"""
    def run():
        from database_service import get_audit_logs
        return {"entries": get_audit_logs(limit=limit)}

    return _safe("audit_logs", run, {"entries": []})


@router.get("/api/live_gate")
def api_live_gate():
    return _live_gate()


def _live_gate():
    """
    LIVE SAFETY GATE 的**唯讀**狀態(第九十二節)。

    ## 為什麼這裡沒有「切換到 LIVE」的按鈕

    第九十二節要求切到 LIVE 前有七項確認與一句「I UNDERSTAND LIVE
    TRADING RISK」。那七項確認在 scripts/live_gate.py 裡,而且結果是
    一個**人手動建立的簽名檔**,有效期 24 小時、必須指名批准的金額。

    做成網頁按鈕會讓那整套變成一個誤觸就會發生的動作。網頁按鈕沒辦法
    表達「這個確認 24 小時後失效」「這次批准的是 30 USDT 不是所有金額」,
    而那兩件事正是這套機制的重點。

    所以這裡只顯示:現在通不通過、哪幾項沒過、確認檔什麼時候過期。
    要開實單仍然要人去 VPS 上跑那個腳本。這是刻意偏離第九十二節的
    UI 部分,理由寫在這裡。
    """
    def run():
        from agmcis.safety.providers import build_gate

        result = build_gate().evaluate()
        payload = result.to_dict()
        payload["how_to_open"] = (
            "在 VPS 上執行 scripts/live_gate.py。它會逐項確認帳戶、交易所、"
            "市場、風險、槓桿、當日虧損上限與 API,最後要求逐字輸入 "
            "I UNDERSTAND LIVE TRADING RISK。確認檔 24 小時後自動失效。"
        )
        return payload

    return _safe("live_gate", run, {
        "open": False, "checks": [], "failed_count": 0,
    })


@router.get("/api/news_center")
def api_news_center(limit: int = Query(10, ge=1, le=50)):
    return _news_center(limit=limit)


def _news_center(limit=10):
    """
    News Center(第八十九節):每則新聞的 Impact / Direction / Confidence。

    **信心是關鍵字比對的信心,不是「這則新聞會讓價格往那邊走」的機率。**
    這兩件事差很遠,而把前者顯示成後者會讓人對一個關鍵字計數器
    產生不該有的信任。欄位名稱旁邊必須寫清楚。
    """
    def run():
        from news_ai_engine import analyze_news_sentiment

        items = analyze_news_sentiment()[:limit]
        rows = []

        for item in items:
            score = float(item.get("score") or 0)
            sentiment = item.get("sentiment") or "Neutral"

            rows.append({
                "title": item.get("title"),
                "source": item.get("source"),
                "url": item.get("url"),
                "direction": sentiment,
                # Impact 用分數的絕對值分級。這是關鍵字命中的強度,
                # 不是市場影響力 —— 一則標題裡有三個「hack」的文章
                # 不見得比一則有一個的重要。
                "impact": (
                    "HIGH" if score >= 45 else
                    "MEDIUM" if score >= 20 else "LOW"
                ),
                "score": score,
                "affected_symbols": item.get("affected_symbols") or [],
                "confidence_basis": "關鍵字比對強度,不是價格影響機率",
            })

        return {"news": rows, "count": len(rows)}

    return _safe("news_center", run, {"news": [], "count": 0})


# /api/journal 已經被 api/journal.py 用掉了(那是舊的簡單版:
# 只有 symbol / action / price / reason)。第四十一節要的是 27 個欄位的
# 完整版,所以走一個新路徑而不是覆蓋舊的 —— 覆蓋會讓現有前端壞掉。
@router.get("/api/trade_journal")
def api_trade_journal(limit: int = Query(30, ge=1, le=200)):
    return _journal(limit=limit)


def _journal(limit=30):
    """
    完整交易日誌(第四十一節)。27 個欄位,拼不起來的部分會標出來。

    `avg_completeness` 是這一批資料本身有多完整 —— **不是績效指標**。
    在拿一批 completeness 60% 的紀錄去算勝率之前,要先知道那件事。
    """
    def run():
        from agmcis.review import journal as journal_module

        from database_service import get_closed_trades, get_decisions

        trades = get_closed_trades()[-limit:]
        decisions = get_decisions(limit=limit * 2)

        entries = journal_module.build_many(trades, decisions=decisions)

        return {
            "entries": [e.to_dict() for e in entries],
            "summary": journal_module.summarise(entries),
        }

    return _safe("journal", run, {
        "entries": [], "summary": {"count": 0, "avg_completeness": 0.0},
    })


@router.get("/api/signals")
def api_signals(limit: int = Query(5, ge=1, le=20)):
    return _signals(limit=limit)


def _signals(limit=5):
    """
    現在的訊號(第八十五節)。

    **包含 WAIT 的訊號。** 只回傳可交易的訊號會讓「系統沒在跑」與
    「系統跑了但結論是觀望」看起來一樣 —— 而後者是第二十九節
    明確認可的合法結論。
    """
    def run():
        from agmcis.signal import pipeline

        rows = []
        for symbol in list(settings.WATCHLIST_SYMBOLS)[:limit]:
            signal = pipeline.analyse_symbol(symbol)
            rows.append(signal.to_dict())

        return {
            "signals": rows,
            "tradable": sum(1 for r in rows if r.get("direction") not in
                            (None, "觀望", "WAIT")),
            "min_score": float(getattr(settings, "MIN_SIGNAL_SCORE", 70)),
        }

    return _safe("signals", run, {"signals": [], "tradable": 0})


@router.get("/api/positions")
def api_positions():
    return _positions()


def _positions():
    """
    持倉(第八十五 / 八十八節)。

    每一筆都帶 `has_stop` —— 沒有停損的部位沒有虧損上限,
    而那是這張表上唯一需要立刻行動的資訊。
    """
    def run():
        from database_service import get_open_trades

        rows = get_open_trades()
        naked = [t["symbol"] for t in rows if t.get("stoploss") is None]

        return {
            "positions": [
                {
                    "symbol": t.get("symbol"),
                    "direction": t.get("signal"),
                    "entry": t.get("entry_price"),
                    "stop_loss": t.get("stoploss"),
                    "take_profit": t.get("takeprofit"),
                    "leverage": t.get("leverage"),
                    "notional": t.get("position_value"),
                    "margin": t.get("size_usdt"),
                    "liquidation_price": t.get("liquidation_price"),
                    "tp_stage": t.get("tp_stage", 0),
                    "realized_partial_usdt": t.get("realized_partial_usdt", 0.0),
                    "opened_at": t.get("opened_at"),
                    "has_stop": t.get("stoploss") is not None,
                }
                for t in rows
            ],
            "count": len(rows),
            "naked": naked,
        }

    return _safe("positions", run, {"positions": [], "count": 0, "naked": []})


@router.get("/api/risk")
def api_risk():
    return _risk()


def _risk():
    """
    風控現況(第八十五節):帳戶層閘門通不通過、為什麼不通過。

    這個端點回答的是「我現在為什麼開不了新倉」。答案幾乎總是
    某一條上限碰到了,而那件事目前只寫在 log 裡。
    """
    def run():
        from agmcis.risk.account_state import build_account_state
        from agmcis.risk.engine import get_engine

        state = build_account_state()
        gate = get_engine().check_gate(state)

        return {
            "allowed": gate.allowed,
            "blockers": list(gate.blockers),
            "warnings": list(gate.warnings),
            "emergency": gate.emergency,
            "account": {
                "equity": state.equity,
                "available_balance": state.available_balance,
                "open_positions": state.open_positions,
                "exposure_pct": round(state.exposure_pct, 2),
                "unrealized_pnl_usdt": state.unrealized_pnl_usdt,
                "realized_pnl_24h": state.realized_pnl_24h,
                "realized_pnl_7d": state.realized_pnl_7d,
                "consecutive_losses": state.consecutive_losses,
                "max_drawdown_pct": state.max_drawdown_pct,
            },
            "limits": settings.risk_limits_dict(),
        }

    return _safe("risk", run, {
        "allowed": False, "blockers": ["RISK_STATE_UNAVAILABLE"],
        "account": {}, "limits": {},
    })


@router.get("/api/transparency_summary")
def api_transparency_summary():
    return _summary()


def _summary():
    """
    一次拿到所有需要注意的事,給頁面頂端的狀態列用。

    **只回報需要注意的東西。** 一切正常時每個計數都是 0,
    那本身就是有意義的資訊。
    """
    orders = _orders(limit=MAX_ORDERS)
    calibration = _calibration()
    reconciliation = _reconciliation()
    strategies = _strategy_health()

    alerts = []

    if strategies.get("paused_count"):
        alerts.append({
            "level": "warning",
            "message": (
                f"{strategies['paused_count']} 個策略被自動停用。"
                f"停用的策略不會投票,系統的訊號來源變少了。"
            ),
        })

    if strategies.get("drifted_count"):
        alerts.append({
            "level": "warning",
            "message": (
                f"{strategies['drifted_count']} 個策略的近期績效顯著低於歷史"
                f"(STRATEGY_DRIFT)。這是「去看一下」,不是「已經壞了」。"
            ),
        })

    if orders.get("naked_count"):
        alerts.append({
            "level": "critical",
            "message": f"{orders['naked_count']} 張單已成交但沒有停損保護",
        })

    if orders.get("unresolved_count"):
        alerts.append({
            "level": "warning",
            "message": f"{orders['unresolved_count']} 張單狀態不明,尚未對帳完成",
        })

    critical = reconciliation.get("critical_count") or 0
    if critical:
        alerts.append({
            "level": "critical",
            "message": f"對帳發現 {critical} 項需要立刻處理的差異",
        })

    config = _config_changes()
    if config.get("risk_increase_count"):
        alerts.append({
            "level": "warning",
            "message": (
                f"風控參數被放寬過 {config['risk_increase_count']} 次。"
                f"「先放寬一下試試看」之後常常沒有人記得改回來。"
            ),
        })

    review = _self_review()
    if review.get("verdict") == "LOSING":
        alerts.append({
            "level": "critical",
            "message": f"自我檢討判定 LOSING:{review.get('headline')}",
        })
    elif review.get("verdict") == "FRAGILE":
        alerts.append({
            "level": "warning",
            "message": f"自我檢討判定 FRAGILE:{review.get('headline')}",
        })

    if not calibration.get("calibrated"):
        alerts.append({
            "level": "warning",
            "message": "合約規格尚未校準,強平價與成本都是估計值",
        })

    for source in (orders, calibration, reconciliation, review, config, strategies):
        if source.get("error"):
            alerts.append({"level": "critical", "message": source["error"]})

    return {
        "alerts": alerts,
        "alert_count": len(alerts),
        "naked_count": orders.get("naked_count", 0),
        "unresolved_count": orders.get("unresolved_count", 0),
        "reconciliation_critical": critical,
        "calibrated": bool(calibration.get("calibrated")),
        "self_review_verdict": review.get("verdict"),
        "config_risk_increases": config.get("risk_increase_count", 0),
        "paused_strategies": strategies.get("paused_count", 0),
        "drifted_strategies": strategies.get("drifted_count", 0),
    }
