from fastapi import APIRouter
from ai_decision_service import get_ai_decisions

router = APIRouter()

@router.get("/api/ai_decisions")
def api_ai_decisions():
    return {
        "decisions": get_ai_decisions()
    }
