import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import APIRouter
from database_service import get_closed_trades

router = APIRouter()

@router.get("/api/equity_curve")
def equity_curve():
    balance = 10000
    curve = [{"index": 0, "balance": balance}]

    for i, t in enumerate(get_closed_trades(), start=1):
        balance += float(t.get("pnl_usdt") or 0)
        curve.append({
            "index": i,
            "balance": round(balance, 2)
        })

    return {"curve": curve}
