from fastapi import APIRouter
import json
from pathlib import Path

router = APIRouter()

STATUS_FILE = Path("scheduler_status.json")

@router.get("/api/scheduler_status")
def scheduler_status():
    if not STATUS_FILE.exists():
        return {
            "status": "offline",
            "message": "scheduler_status.json not found"
        }

    try:
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }
