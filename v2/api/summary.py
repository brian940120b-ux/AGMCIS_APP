from fastapi import APIRouter

router = APIRouter()

@router.get("/api/summary")
def summary():
    return {
        "balance": 10000,
        "open_positions": 0,
        "win_rate": 0,
        "today_pnl": 0
    }
