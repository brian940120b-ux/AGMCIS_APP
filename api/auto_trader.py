from fastapi import APIRouter
from auto_trader import run_auto_trader

router = APIRouter()

@router.get("/api/auto_trader")
def api_auto_trader():
    return run_auto_trader()
