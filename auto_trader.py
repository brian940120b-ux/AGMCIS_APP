from scanner_service import scan_market
from logger_service import logger

def run_auto_trader():
    data = scan_market()
    top = data[:3]

    summary = " | ".join(
        f'{d["symbol"]}:{d["trade_signal"]}:{d["confidence"]}%:{d.get("blocked_reason","OK")}'
        for d in top
    )

    logger.info(f"Auto Trader | NO_BUY_SIGNAL | {summary}")

    return {
        "status": "NO_BUY_SIGNAL",
        "top_candidates": top
    }
