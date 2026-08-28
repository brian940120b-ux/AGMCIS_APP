"""
Dashboard Payload

單一組裝點：REST (/api/dashboard) 與 WebSocket (/ws) 送出的內容完全一致，
前端只要寫一份 render 邏輯。

payload 內含 status 區塊，任何資料源失敗都會在這裡回報，
Dashboard 會顯示 degraded 而不是整頁空白。
"""
import time

from config import (
    AI_DECISION_LIMIT,
    MARKET_SCAN_LIMIT,
    START_BALANCE,
    VERSION,
)
from services.core import (
    load_ai_decisions,
    load_closed_trades,
    load_market_scan,
    load_portfolio,
)

_CACHE = {"payload": None, "ts": 0.0}


def build_equity_curve(closed_trades, final_balance=None):
    """
    由已平倉交易還原權益曲線。

    終點對齊帳戶目前餘額（起點反推），避免曲線終值跟 KPI 卡片對不起來。
    """
    total_pnl = sum(float(t.get("pnl_usdt") or 0) for t in closed_trades)

    if final_balance:
        balance = float(final_balance) - total_pnl
    else:
        balance = START_BALANCE

    curve = [{"index": 0, "balance": round(balance, 2)}]

    for i, trade in enumerate(closed_trades, 1):
        balance += float(trade.get("pnl_usdt") or 0)
        curve.append({"index": i, "balance": round(balance, 2)})

    return curve


def build_dashboard_payload():
    """實際重新計算一份 payload（會打 DB 與交易所，屬於阻塞操作）。"""
    errors = []

    portfolio, err = load_portfolio()
    if err:
        errors.append(err)

    market, err = load_market_scan()
    if err:
        errors.append(err)

    ai_decisions, err = load_ai_decisions()
    if err:
        errors.append(err)

    closed_trades, err = load_closed_trades()
    if err:
        errors.append(err)

    # AI Decision Center 以持倉決策為主；沒有持倉時退回掃描結果，面板才不會空著
    if not ai_decisions:
        ai_decisions = market

    portfolio = dict(portfolio)
    portfolio["equity_curve"] = build_equity_curve(
        closed_trades,
        portfolio.get("balance"),
    )

    return {
        "type": "dashboard_update",
        "version": VERSION,
        "timestamp": int(time.time()),
        "status": {
            "state": "degraded" if errors else "live",
            "errors": errors,
        },
        "summary": {
            "balance": portfolio.get("balance"),
            "open_positions": portfolio.get("open_positions"),
            "risk": portfolio.get("risk_level"),
            "upnl": portfolio.get("total_open_upnl"),
        },
        "portfolio": portfolio,
        "market_scan": market[:MARKET_SCAN_LIMIT],
        "ai_decisions": ai_decisions[:AI_DECISION_LIMIT],
    }


def get_dashboard_payload(max_age=0):
    """
    取得 payload，max_age 秒內直接用快取。

    WebSocket 廣播與 REST 共用同一份快取，新分頁連進來可以立刻拿到畫面，
    不必等下一輪掃描，也不會多打一次交易所 API。
    """
    now = time.time()
    cached = _CACHE["payload"]

    if cached and max_age and now - _CACHE["ts"] < max_age:
        return cached

    payload = build_dashboard_payload()
    _CACHE["payload"] = payload
    _CACHE["ts"] = now
    return payload


def get_cached_payload():
    """只讀快取，沒有就回 None（給剛連上的 WebSocket 用）。"""
    return _CACHE["payload"]
