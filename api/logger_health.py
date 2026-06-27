from fastapi import APIRouter
from pathlib import Path

router = APIRouter()

@router.get("/api/logger_health")
def logger_health():
    log_file = Path("logs/agmcis.log")

    if not log_file.exists():
        return {
            "status": "NO_LOG",
            "lines": []
        }

    lines = log_file.read_text(errors="ignore").splitlines()

    return {
        "status": "OK",
        "line_count": len(lines),
        "last_20": lines[-20:]
    }
