import sys
from pathlib import Path

# 使用 v1/v2 已驗證的核心模組
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from portfolio_manager import get_portfolio_summary
from scanner_service import scan_market
from database_service import get_closed_trades

def build_equity_curve():
    balance=10000
    curve=[{"index":0,"balance":balance}]
    for i,t in enumerate(get_closed_trades(),1):
        balance+=float(t.get("pnl_usdt") or 0)
        curve.append({"index":i,"balance":round(balance,2)})
    return curve

def get_dashboard_payload():
    portfolio = get_portfolio_summary()
    market = scan_market()

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
            "equity_curve": build_equity_curve()
        },
        "market_scan": market[:10],
        "ai_decisions": market[:5]
    }
