from fastapi import APIRouter
from database_service import get_closed_trades

router = APIRouter()

@router.get("/api/leaderboard")
def api_leaderboard():
    trades = get_closed_trades()

    sorted_trades = sorted(
        trades,
        key=lambda x: x.get("pnl_usdt") or 0,
        reverse=True
    )

    return {
        "winners": sorted_trades[:5],
        "losers": sorted_trades[-5:]
    }
