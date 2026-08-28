"""
v3 資料存取層

v3 不重寫交易邏輯，直接重用 v1 已驗證的核心模組
（portfolio_manager / scanner_service / ai_decision_service / database_service）。

但那些模組需要 PostgreSQL 與交易所 API，任何一項不可用時，
整個 Dashboard 就會跟著掛掉。這一層負責三件事：

1. 延遲載入：import 失敗不會讓 app 起不來
2. 例外隔離：單一資料源失敗不影響其他區塊
3. 回報狀態：讓前端知道現在是 live 還是 degraded
"""
import sys
from pathlib import Path

# 使用 v1/v2 已驗證的核心模組
ROOT = str(Path(__file__).resolve().parents[2])
if ROOT not in sys.path:
    sys.path.append(ROOT)


EMPTY_PORTFOLIO = {
    "balance": 0,
    "open_positions": 0,
    "total_exposure": 0,
    "exposure_ratio": 0,
    "risk_level": "無資料",
    "allocation": [],
    "open_trades": [],
    "total_open_upnl": 0,
}


def _safe(loader, fallback, source):
    """執行 loader，失敗時回傳 fallback 與錯誤訊息。"""
    try:
        return loader(), None
    except Exception as exc:
        return fallback, f"{source}: {type(exc).__name__}: {exc}"


def load_portfolio():
    def loader():
        from portfolio_manager import get_portfolio_summary
        return get_portfolio_summary()

    return _safe(loader, dict(EMPTY_PORTFOLIO), "portfolio")


def load_market_scan():
    def loader():
        from scanner_service import scan_market
        return scan_market()

    return _safe(loader, [], "market_scan")


def load_ai_decisions():
    def loader():
        from ai_decision_service import get_ai_decisions
        return get_ai_decisions()

    return _safe(loader, [], "ai_decisions")


def load_closed_trades():
    def loader():
        from database_service import get_closed_trades
        return get_closed_trades()

    return _safe(loader, [], "closed_trades")
