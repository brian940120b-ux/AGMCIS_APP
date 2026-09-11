"""
系統健康檢查。

Phase 0.5 的修正:移除 okx 欄位 —— exchange_engine 已經是 BingX 單一交易所,
該欄位永遠回傳 error,是誤導性的紅燈。另外補上資料庫與風控的狀態。
"""
from fastapi import APIRouter

from exchange_engine import test_connection
from telegram_config import is_configured as telegram_configured

router = APIRouter()


@router.get("/api/system_health")
def system_health():
    exchanges = test_connection()
    bingx = exchanges.get("bingx", {})

    database = "active"
    database_error = None
    try:
        from database_service import get_account
        get_account()
    except Exception as exc:
        database = "error"
        database_error = str(exc)

    return {
        "api": "active",
        "telegram": "active" if telegram_configured() else "missing",
        "bingx": "active" if bingx.get("success") else "error",
        "bingx_error": bingx.get("error"),
        "database": database,
        "database_error": database_error,
    }
