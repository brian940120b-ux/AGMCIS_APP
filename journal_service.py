from trade_journal import log_trade

def log_open(symbol, signal, price, reason="Paper trade opened", score=None):
    log_trade(
        symbol=symbol,
        signal=signal,
        action="OPEN",
        price=price,
        reason=reason,
        score=score
    )

def log_close(symbol, signal, price, pnl_usdt, pnl_pct, reason="Paper trade closed"):
    log_trade(
        symbol=symbol,
        signal=signal,
        action="CLOSE",
        price=price,
        reason=reason,
        pnl_usdt=pnl_usdt,
        pnl_pct=pnl_pct
    )
