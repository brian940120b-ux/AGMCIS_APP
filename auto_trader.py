from scanner_service import scan_market
from paper_trading import create_paper_trade

def run_auto_trader():
    data = scan_market()
    for d in data:
        if d["trade_signal"]=="🟢 Buy":
            return create_paper_trade(
                symbol=d["symbol"],
                entry_price=d["entry_price"],
                signal="LONG",
                stoploss=d["stoploss"],
                takeprofit=d["takeprofit"]
            )

    return {
        "status":"NO_BUY_SIGNAL",
        "top_candidates": data[:3]
    }
