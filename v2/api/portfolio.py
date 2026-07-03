import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import APIRouter
from portfolio_manager import get_portfolio_summary

router = APIRouter()

@router.get("/api/portfolio")
def portfolio():
    return get_portfolio_summary()
