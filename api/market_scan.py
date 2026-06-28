from fastapi import APIRouter
from scanner_service import scan_market

router = APIRouter()

@router.get("/api/market_scan")
def api_market_scan():
    return {
        "top": scan_market()[:10]
    }
