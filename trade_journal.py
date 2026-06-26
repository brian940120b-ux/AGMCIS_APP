from db import get_connection

def log_trade(symbol, signal, action, price, reason="", score=None, pnl_usdt=None, pnl_pct=None):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO trade_journal
        (symbol, signal, action, price, reason, score, pnl_usdt, pnl_pct)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        symbol,
        signal,
        action,
        price,
        reason,
        score,
        pnl_usdt,
        pnl_pct
    ))

    conn.commit()
    cur.close()
    conn.close()
