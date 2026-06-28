from scanner_service import scan_market
from paper_trading import create_paper_trade
from logger_service import logger

def run_auto_trader():
    data = scan_market()
    top = data[:3]

    for d in data:
        if d.get("trade_signal") in ["🟢 Buy", "🟢 Strong Buy"]:
            result = create_paper_trade(
                symbol=d.get("symbol"),
                entry_price=d.get("entry_price"),
                signal="做多",
                stoploss=d.get("stoploss"),
                takeprofit=d.get("takeprofit")
            )
            logger.info(f'Auto Trader | OPEN_ATTEMPT | {d.get("symbol")} | {result.get("message")}')
            return {"status":"OPEN_ATTEMPT","candidate":d,"result":result}

    logger.info("Auto Trader | NO_BUY_SIGNAL")
    return {"status":"NO_BUY_SIGNAL","top_candidates":top}
