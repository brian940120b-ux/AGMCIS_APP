from fastapi import APIRouter
from journal_service import get_recent

router = APIRouter()

@router.get("/api/journal")
def api_journal():
    rows = get_recent(20)
    return [
        {
            "symbol": r[0],
            "action": r[1],
            "price": float(r[2]),
            "reason": r[3],
            "created_at": str(r[4])
        }
        for r in rows
    ]
