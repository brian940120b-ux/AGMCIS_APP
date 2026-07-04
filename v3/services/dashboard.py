import sys
from pathlib import Path

# 使用 v1/v2 已驗證的核心模組
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from portfolio_manager import get_portfolio_summary
from scanner_service import scan_market

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
        "portfolio": portfolio,
        "market_scan": market[:10],
        "ai_decisions": market[:5]
    }
