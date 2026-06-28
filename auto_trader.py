from scanner_service import scan_market
from paper_trading import create_paper_trade
from logger_service import logger
from database_service import get_open_trades, get_open_trade

MAX_AUTO_POSITIONS = 3

def run_auto_trader():
    open_trades = get_open_trades(source="AUTO")
    if len(open_trades) >= MAX_AUTO_POSITIONS:
        return {"status":"BLOCKED_MAX_POSITIONS","open_positions":len(open_trades)}

    data = scan_market()
    top = data[:3]

    for d in data:
        if get_open_trade(d.get("symbol")):
            continue

        if d.get("trade_signal") in ["🟢 Buy", "🟢 Strong Buy"]:
            result = create_paper_trade(
                symbol=d.get("symbol"),
                entry_price=d.get("entry_price"),
                signal="做多",
                stoploss=d.get("stoploss"),
                takeprofit=d.get("takeprofit"),
                source="AUTO"
            )
            logger.info(f'Auto Trader | OPEN_ATTEMPT | {d.get("symbol")} | {result.get("message")}')
            return {"status":"OPEN_ATTEMPT","candidate":d,"result":result}

    logger.info("Auto Trader | NO_BUY_SIGNAL")
    return {"status":"NO_BUY_SIGNAL","top_candidates":top}
