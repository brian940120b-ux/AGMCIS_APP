import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import APIRouter
from exchange_engine import test_connection
from notifier import BOT_TOKEN, CHAT_ID

router = APIRouter()

@router.get("/api/system_health")
def system_health():
    exchanges = test_connection()
    return {
        "api": "active",
        "telegram": "active" if BOT_TOKEN and CHAT_ID else "missing",
        "bingx": "active" if exchanges.get("bingx", {}).get("success") else "error"
    }
