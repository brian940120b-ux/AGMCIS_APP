import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import APIRouter
from scanner_service import scan_market

router = APIRouter()

@router.get("/api/ai_decisions")
def ai_decisions():
    data = scan_market()
    return {
        "decisions": data[:5]
    }
