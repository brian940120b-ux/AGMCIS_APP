"""
權益曲線。起始資金取自 config.PAPER_START_BALANCE,不再寫死 10000。
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from fastapi import APIRouter

from config import PAPER_START_BALANCE
from database_service import get_closed_trades

router = APIRouter()


@router.get("/api/equity_curve")
def api_equity_curve():
    balance = float(PAPER_START_BALANCE)
    points = [{"index": 0, "balance": round(balance, 2)}]

    for i, trade in enumerate(get_closed_trades(), start=1):
        balance += float(trade.get("pnl_usdt") or 0)
        points.append({
            "index": i,
            "symbol": trade.get("symbol"),
            "pnl": float(trade.get("pnl_usdt") or 0),
            "balance": round(balance, 2),
            "reason": trade.get("close_reason"),
            "pnl_basis": trade.get("pnl_basis"),
        })

    return {
        "start_balance": round(float(PAPER_START_BALANCE), 2),
        "current_balance": round(balance, 2),
        "points": points,
    }
