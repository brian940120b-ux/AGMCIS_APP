from db import get_connection


def get_account():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT balance, wins, losses, trades
        FROM accounts
        ORDER BY id DESC
        LIMIT 1;
        """
    )

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


def get_trades():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
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
            closed_at
        FROM trades
        ORDER BY id ASC;
        """
    )

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
            "closed_at": row[9].strftime("%Y-%m-%d %H:%M:%S") if row[9] else None
        })

    return trades


def get_open_trades():
    trades = get_trades()

    return [
        trade for trade in trades
        if trade["status"] == "OPEN"
    ]


def get_closed_trades():
    trades = get_trades()

    return [
        trade for trade in trades
        if trade["status"] == "CLOSED"
    ]


def insert_trade(trade):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO trades (
            symbol,
            signal,
            entry_price,
            exit_price,
            size_usdt,
            status,
            pnl_pct,
            pnl_usdt,
            opened_at,
            closed_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s);
        """,
        (
            trade.get("symbol"),
            trade.get("signal"),
            trade.get("entry_price"),
            trade.get("exit_price"),
            trade.get("size_usdt"),
            trade.get("status", "OPEN"),
            trade.get("pnl_pct"),
            trade.get("pnl_usdt"),
            trade.get("closed_at")
        )
    )

    conn.commit()
    cur.close()
    conn.close()


def update_account(balance, wins, losses, trades):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("DELETE FROM accounts;")

    cur.execute(
        """
        INSERT INTO accounts (
            balance,
            wins,
            losses,
            trades
        )
        VALUES (%s, %s, %s, %s);
        """,
        (
            balance,
            wins,
            losses,
            trades
        )
    )

    conn.commit()
    cur.close()
    conn.close()