import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import APIRouter
from scanner_service import scan_market

router = APIRouter()

@router.get("/api/market_scan")
def market_scan():
    data = scan_market()
    return {
        "top": data[:10]
    }
