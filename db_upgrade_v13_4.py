from db import get_connection


def main():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS stoploss NUMERIC(18,8);")
    cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS takeprofit NUMERIC(18,8);")
    cur.execute("ALTER TABLE trades ADD COLUMN IF NOT EXISTS close_reason VARCHAR(100);")

    conn.commit()
    cur.close()
    conn.close()

    print("V13.4 trades table upgrade complete")


if __name__ == "__main__":
    main()