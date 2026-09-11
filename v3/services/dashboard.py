import sys
from pathlib import Path

# 使用 v1/v2 已驗證的核心模組
# 用 append：v3 自己的模組（例如 config）必須優先於專案根目錄的同名模組
ROOT = str(Path(__file__).resolve().parents[2])
if ROOT not in sys.path:
    sys.path.append(ROOT)

from portfolio_manager import get_portfolio_summary
from scanner_service import scan_market
from database_service import get_closed_trades

START_BALANCE = 10000


def build_equity_curve():
    balance = START_BALANCE
    curve = [{"index": 0, "balance": balance}]
    for i, t in enumerate(get_closed_trades(), 1):
        balance += float(t.get("pnl_usdt") or 0)
        curve.append({"index": i, "balance": round(balance, 2)})
    return curve


def get_dashboard_payload():
    # 任何一段資料來源掛掉都不該讓整個 dashboard 斷線，改成回傳 errors 讓前端顯示
    errors = []

    try:
        portfolio = get_portfolio_summary()
    except Exception as exc:
        portfolio = {}
        errors.append(f"portfolio: {exc}")

    try:
        market = scan_market() or []
    except Exception as exc:
        market = []
        errors.append(f"market_scan: {exc}")

    try:
        equity_curve = build_equity_curve()
    except Exception as exc:
        equity_curve = []
        errors.append(f"equity_curve: {exc}")

    return {
        "type": "dashboard_update",
        "summary": {
            "balance": portfolio.get("balance"),
            "open_positions": portfolio.get("open_positions"),
            "risk": portfolio.get("risk_level"),
            "upnl": portfolio.get("total_open_upnl")
        },
        "portfolio": {
            **portfolio,
            "equity_curve": equity_curve
        },
        "market_scan": market[:10],
        "ai_decisions": market[:5],
        "errors": errors
    }
