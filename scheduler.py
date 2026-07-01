import time
import json
from datetime import datetime
from position_monitor import run_position_monitor
from auto_trader import run_auto_trader
from logger_service import logger

def write_status(status, monitor_result=None, trader_result=None, errors=0):
    data = {
        "status": status,
        "last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "monitor_closed": (monitor_result or {}).get("closed_count", 0),
        "trader_status": (trader_result or {}).get("status"),
        "errors": errors
    }
    with open("scheduler_status.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def run_once():
    monitor_result = run_position_monitor()
    trader_result = run_auto_trader()

    write_status("running", monitor_result, trader_result)
    logger.info(f"Scheduler | monitor={monitor_result.get('closed_count')} | trader={trader_result.get('status')}")

    return {
        "monitor": monitor_result,
        "trader": trader_result
    }

def run_loop(interval=60):
    logger.info(f"Scheduler | START | interval={interval}s")

    while True:
        try:
            run_once()
        except Exception as e:
            logger.exception(f"Scheduler | ERROR | {e}")

        time.sleep(interval)

if __name__ == "__main__":
    run_loop(60)

def write_status(status, monitor_result=None, trader_result=None, errors=0):
    data = {
        "status": status,
        "last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "monitor_closed": (monitor_result or {}).get("closed_count", 0),
        "trader_status": (trader_result or {}).get("status"),
        "errors": errors
    }

    with open("scheduler_status.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


