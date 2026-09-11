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

    alerts = []

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

    for source in (orders, calibration, reconciliation, review):
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
    }
