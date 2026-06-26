from fastapi import APIRouter
from stats_service import get_stats

router = APIRouter()

@router.get("/api/stats")
def api_stats():
    return get_stats()
