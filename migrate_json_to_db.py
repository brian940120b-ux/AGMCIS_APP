import json
from datetime import datetime

from db import get_connection


ACCOUNT_FILE = "data/paper_account.json"
TRADES_FILE = "data/paper_trades.json"


def parse_time(value):
    if not value:
        return None

    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def migrate_account():
    with open(ACCOUNT_FILE, "r", encoding="utf-8") as file:
        account = json.load(file)

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("DELETE FROM accounts;")

    cur.execute(
        """
        INSERT INTO accounts (balance, wins, losses, trades)
        VALUES (%s, %s, %s, %s);
        """,
        (
            account.get("balance", 10000),
            account.get("wins", 0),
            account.get("losses", 0),
            account.get("trades", 0)
        )
    )

    conn.commit()
    cur.close()
    conn.close()


def migrate_trades():
    with open(TRADES_FILE, "r", encoding="utf-8") as file:
        trades = json.load(file)

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("DELETE FROM trades;")

    for trade in trades:
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
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (
                trade.get("symbol"),
                trade.get("signal"),
                trade.get("entry_price"),
                trade.get("exit_price"),
                trade.get("size_usdt"),
                trade.get("status"),
                trade.get("pnl_pct"),
                trade.get("pnl_usdt"),
                parse_time(trade.get("opened_at")),
                parse_time(trade.get("closed_at"))
            )
        )

    conn.commit()
    cur.close()
    conn.close()


def main():
    print()
    print("========== AGMCIS V13.2 JSON → PostgreSQL Migration ==========")

    migrate_account()
    print("accounts 遷移完成")

    migrate_trades()
    print("trades 遷移完成")

    print("==============================================================")


if __name__ == "__main__":
    main()