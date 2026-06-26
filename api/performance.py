from fastapi import APIRouter
from performance_service import get_performance_summary

router = APIRouter()

@router.get("/api/performance")
def api_performance():
    return get_performance_summary()
