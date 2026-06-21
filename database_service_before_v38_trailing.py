from db import get_connection


def get_account():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT balance, wins, losses, trades
        FROM accounts
        ORDER BY id DESC
        LIMIT 1;
    """)

    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return {
            "balance": 10000,
            "wins": 0,
            "losses": 0,
            "trades": 0
        }

    return {
        "balance": float(row[0]),
        "wins": int(row[1]),
        "losses": int(row[2]),
        "trades": int(row[3])
    }


def update_account(balance, wins, losses, trades):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("DELETE FROM accounts;")

    cur.execute("""
        INSERT INTO accounts (balance, wins, losses, trades)
        VALUES (%s, %s, %s, %s);
    """, (balance, wins, losses, trades))

    conn.commit()
    cur.close()
    conn.close()


def get_trades():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            symbol,
            signal,
            entry_price,
            exit_price,
            size_usdt,
            status,
            pnl_pct,
            pnl_usdt,
            opened_at,
            closed_at,
            stoploss,
            takeprofit,
            close_reason
        FROM trades
        ORDER BY id ASC;
    """)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    trades = []

    for row in rows:
        trades.append({
            "symbol": row[0],
            "signal": row[1],
            "entry_price": float(row[2]) if row[2] is not None else None,
            "exit_price": float(row[3]) if row[3] is not None else None,
            "size_usdt": float(row[4]) if row[4] is not None else None,
            "status": row[5],
            "pnl_pct": float(row[6]) if row[6] is not None else None,
            "pnl_usdt": float(row[7]) if row[7] is not None else None,
            "opened_at": row[8].strftime("%Y-%m-%d %H:%M:%S") if row[8] else None,
            "closed_at": row[9].strftime("%Y-%m-%d %H:%M:%S") if row[9] else None,
            "stoploss": float(row[10]) if row[10] is not None else None,
            "takeprofit": float(row[11]) if row[11] is not None else None,
            "close_reason": row[12]
        })

    return trades


def get_open_trades():
    return [
        trade for trade in get_trades()
        if trade["status"] == "OPEN"
    ]


def get_closed_trades():
    return [
        trade for trade in get_trades()
        if trade["status"] == "CLOSED"
    ]


def get_open_trade(symbol):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id
        FROM trades
        WHERE symbol = %s AND status = 'OPEN'
        ORDER BY id DESC
        LIMIT 1;
    """, (symbol,))

    row = cur.fetchone()
    cur.close()
    conn.close()

    return row[0] if row else None


def insert_trade(symbol, signal, entry_price, size_usdt, stoploss=None, takeprofit=None, leverage=3, position_value=3000):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO trades (
            symbol,
            signal,
            entry_price,
            size_usdt,
            status,
            stoploss,
            takeprofit,
            leverage,
            position_value,
            opened_at
        )
        VALUES (%s, %s, %s, %s, 'OPEN', %s, %s, %s, %s, CURRENT_TIMESTAMP);
    """, (
        symbol,
        signal,
        entry_price,
        size_usdt,
        stoploss,
        takeprofit,
        leverage,
        position_value
    ))

    conn.commit()
    cur.close()
    conn.close()


def close_trade(symbol, exit_price, pnl_pct, pnl_usdt, close_reason):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE trades
        SET
            status = 'CLOSED',
            exit_price = %s,
            pnl_pct = %s,
            pnl_usdt = %s,
            close_reason = %s,
            closed_at = CURRENT_TIMESTAMP
        WHERE id = (
            SELECT id
            FROM trades
            WHERE symbol = %s AND status = 'OPEN'
            ORDER BY id DESC
            LIMIT 1
        );
    """, (
        exit_price,
        pnl_pct,
        pnl_usdt,
        close_reason,
        symbol
    ))

    conn.commit()
    cur.close()
    conn.close()