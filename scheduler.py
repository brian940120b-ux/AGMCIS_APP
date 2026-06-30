import time
from position_monitor import run_position_monitor
from auto_trader import run_auto_trader
from logger_service import logger

def run_once():
    monitor_result = run_position_monitor()
    trader_result = run_auto_trader()

    logger.info(f"Scheduler | monitor={monitor_result.get('closed_count')} | trader={trader_result.get('status')}")

    return {
        "monitor": monitor_result,
        "trader": trader_result
    }
