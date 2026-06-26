from fastapi import APIRouter
from portfolio_service import get_portfolio

router = APIRouter()

@router.get("/api/portfolio")
def api_portfolio():
    return get_portfolio()
