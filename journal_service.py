from db import get_connection
from trade_journal import log_trade

def log_open(symbol, signal, price, reason="Paper trade opened", score=None):
    log_trade(symbol, signal, "OPEN", price, reason, score)

def log_close(symbol, signal, price, pnl_usdt, pnl_pct, reason="Paper trade closed"):
    log_trade(symbol, signal, "CLOSE", price, reason, None, pnl_usdt, pnl_pct)

def get_recent(limit=10):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT symbol, action, price, reason, created_at
        FROM trade_journal
        ORDER BY created_at DESC
        LIMIT %s
    """, (limit,))

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return rows
