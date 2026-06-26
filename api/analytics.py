from fastapi import APIRouter
from database_service import get_closed_trades

router = APIRouter()

@router.get("/api/analytics_pro")
def api_analytics_pro():
    trades = get_closed_trades()

    closed = [t for t in trades if t.get("status") == "CLOSED"]
    total_realized = round(sum(float(t.get("pnl_usdt") or 0) for t in closed), 2)

    max_win_streak = 0
    max_loss_streak = 0
    win_streak = 0
    loss_streak = 0

    for t in closed:
        pnl = float(t.get("pnl_usdt") or 0)
        if pnl > 0:
            win_streak += 1
            loss_streak = 0
        else:
            loss_streak += 1
            win_streak = 0

        max_win_streak = max(max_win_streak, win_streak)
        max_loss_streak = max(max_loss_streak, loss_streak)

    return {
        "closed_trades": len(closed),
        "total_realized": total_realized,
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak
    }
