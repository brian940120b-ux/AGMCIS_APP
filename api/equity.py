from fastapi import APIRouter
from database_service import get_closed_trades

router = APIRouter()

@router.get("/api/equity_curve")
def api_equity_curve():
    closed = get_closed_trades()

    balance = 10000
    points = []

    for i, t in enumerate(closed):
        balance += float(t.get("pnl_usdt") or 0)
        points.append({
            "index": i + 1,
            "balance": round(balance, 2)
        })

    return {
        "points": points
    }
