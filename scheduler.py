"""
排程器:每輪檢查持倉(TP/SL)再嘗試自動開倉。

Phase 0.5 的修正:
  1. write_status() 原本定義兩次(第二份在 if __name__ 之後),移除重複。
  2. 狀態檔改記錄真實平倉數與風控 blocker —— 原本 position_monitor 的
     closed_count 寫死為 0,狀態面板永遠顯示 monitor=0。
  3. 每輪例外都會寫進狀態檔,不再只留在 log 裡。

⚠️ 已知重複:auto_runner.py(300 秒)與 opportunity_runner.py(1800 秒)
   也各自會觸發開倉。Phase 1 會收斂成單一排程器。
"""
import json
import time
from datetime import datetime

from auto_trader import run_auto_trader
from logger_service import logger
from position_monitor import run_position_monitor

STATUS_FILE = "scheduler_status.json"


def write_status(status, monitor_result=None, trader_result=None, error=None):
    monitor_result = monitor_result or {}
    trader_result = trader_result or {}

    data = {
        "status": status,
        "last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "monitor_checked": monitor_result.get("checked", 0),
        "monitor_closed": monitor_result.get("closed_count", 0),
        "monitor_skipped": len(monitor_result.get("skipped", [])),
        "monitor_unprotected": len(monitor_result.get("unprotected", [])),
        "trader_status": trader_result.get("status"),
        "trader_reason": trader_result.get("reason"),
        "error": error,
    }

    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return data


def run_once():
    monitor_result = run_position_monitor()
    trader_result = run_auto_trader()

    write_status("running", monitor_result, trader_result)

    logger.info(
        "Scheduler | checked=%d closed=%d skipped=%d unprotected=%d | trader=%s%s",
        monitor_result.get("checked", 0),
        monitor_result.get("closed_count", 0),
        len(monitor_result.get("skipped", [])),
        len(monitor_result.get("unprotected", [])),
        trader_result.get("status"),
        f" ({trader_result.get('reason')})" if trader_result.get("reason") else "",
    )

    return {"monitor": monitor_result, "trader": trader_result}


def run_loop(interval=60):
    logger.info("Scheduler | START | interval=%ds", interval)

    while True:
        try:
            run_once()
        except Exception as exc:
            logger.exception("Scheduler | ERROR | %s", exc)
            try:
                write_status("error", error=str(exc))
            except Exception:
                logger.exception("Scheduler | 無法寫入狀態檔")

        time.sleep(interval)


if __name__ == "__main__":
    run_loop(60)
