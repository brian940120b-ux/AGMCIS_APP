"""
交易與帳戶資料存取層。

Phase 0.5 的三個關鍵修正:
  1. get_trades() 補回 leverage / position_value / id / pnl_basis。
     原本 SELECT 漏掉 leverage,導致所有讀到 trade["leverage"] 的地方
     都拿到 None 並 fallback 成 3x —— 不管實際開倉是 2x 還是 8x。
  2. 平倉改為 close_trade_atomic():鎖定倉位、算損益、更新交易、更新帳戶
     全部在同一個 transaction 內完成。原本「先更新帳戶再更新交易」的兩段式寫法,
     在兩條平倉路徑同時觸發時會把同一筆損益算進餘額兩次。
  3. 已實現損益乘上實際槓桿,與未實現損益的算法一致。
     size_usdt 是保證金,名目價值 = size_usdt × leverage,
     所以 pnl_usdt = size_usdt × leverage × 價格變動比例,pnl_pct 則是保證金 ROI。
"""
import logging

import psycopg2

from db import get_connection, transaction
from direction import price_change_pct, is_directional

logger = logging.getLogger("AGMCIS")

DEFAULT_LEVERAGE = 1.0

TRADE_COLUMNS = """
    id,
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
    close_reason,
    source,
    leverage,
    position_value,
    pnl_basis
"""


class DuplicateOpenTradeError(Exception):
    """同一 symbol 已有 OPEN 倉位。由資料庫的 partial unique index 保證,應用層無法繞過。"""


def _f(value):
    return float(value) if value is not None else None


def _row_to_trade(row):
    return {
        "id": row[0],
        "symbol": row[1],
        "signal": row[2],
        "entry_price": _f(row[3]),
        "exit_price": _f(row[4]),
        "size_usdt": _f(row[5]),
        "status": row[6],
        "pnl_pct": _f(row[7]),
        "pnl_usdt": _f(row[8]),
        "opened_at": row[9].strftime("%Y-%m-%d %H:%M:%S") if row[9] else None,
        "closed_at": row[10].strftime("%Y-%m-%d %H:%M:%S") if row[10] else None,
        "stoploss": _f(row[11]),
        "takeprofit": _f(row[12]),
        "close_reason": row[13],
        "source": row[14] or "MANUAL",
        "leverage": _f(row[15]) or DEFAULT_LEVERAGE,
        "position_value": _f(row[16]),
        "pnl_basis": row[17],
    }


# ---------------- 帳戶 ----------------

def get_account():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT balance, wins, losses, trades
                FROM accounts
                ORDER BY id DESC
                LIMIT 1;
            """)
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return {"balance": 10000, "wins": 0, "losses": 0, "trades": 0}

    return {
        "balance": float(row[0]),
        "wins": int(row[1]),
        "losses": int(row[2]),
        "trades": int(row[3]),
    }


def _write_account(cur, balance, wins, losses, trades):
    """
    UPDATE 最新一列,沒有列才 INSERT。
    原本是 DELETE 全表再 INSERT —— 兩個語句之間若中斷,帳戶資料會直接消失。
    """
    cur.execute("SELECT id FROM accounts ORDER BY id DESC LIMIT 1 FOR UPDATE;")
    row = cur.fetchone()

    if row:
        cur.execute(
            """
            UPDATE accounts
               SET balance = %s, wins = %s, losses = %s, trades = %s
             WHERE id = %s;
            """,
            (balance, wins, losses, trades, row[0]),
        )
    else:
        cur.execute(
            """
            INSERT INTO accounts (balance, wins, losses, trades)
            VALUES (%s, %s, %s, %s);
            """,
            (balance, wins, losses, trades),
        )


def update_account(balance, wins, losses, trades):
    with transaction() as cur:
        _write_account(cur, balance, wins, losses, trades)


# ---------------- 交易讀取 ----------------

def get_trades():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {TRADE_COLUMNS} FROM trades ORDER BY id ASC;")
            rows = cur.fetchall()
    finally:
        conn.close()

    return [_row_to_trade(row) for row in rows]


def _query_trades(where_sql, params=()):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {TRADE_COLUMNS} FROM trades WHERE {where_sql} ORDER BY id ASC;",
                params,
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return [_row_to_trade(row) for row in rows]


def get_open_trades(source=None):
    """改為由資料庫過濾,不再撈全表後在 Python 端篩選。"""
    if source:
        return _query_trades("status = 'OPEN' AND source = %s", (source,))
    return _query_trades("status = 'OPEN'")


def get_closed_trades():
    return _query_trades("status = 'CLOSED'")


def get_open_trade(symbol):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM trades
                 WHERE symbol = %s AND status = 'OPEN'
                 ORDER BY id DESC LIMIT 1;
                """,
                (symbol,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    return row[0] if row else None


# ---------------- 風控用統計 ----------------

def get_realized_pnl_since(hours=24):
    """指定時數內的已實現損益總和。供每日虧損上限使用。"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(pnl_usdt), 0)
                  FROM trades
                 WHERE status = 'CLOSED'
                   AND closed_at >= CURRENT_TIMESTAMP - (%s || ' hours')::interval;
                """,
                (str(int(hours)),),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    return float(row[0]) if row and row[0] is not None else 0.0


def count_trades_since(hours=24):
    """指定時數內的開倉筆數。供單日交易次數上限使用。"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM trades
                 WHERE opened_at >= CURRENT_TIMESTAMP - (%s || ' hours')::interval;
                """,
                (str(int(hours)),),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    return int(row[0]) if row else 0


def get_consecutive_losses():
    """從最近一筆已平倉往回數,連續虧損筆數。供連續虧損熔斷使用。"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT pnl_usdt FROM trades
                 WHERE status = 'CLOSED'
                 ORDER BY closed_at DESC NULLS LAST, id DESC
                 LIMIT 50;
            """)
            rows = cur.fetchall()
    finally:
        conn.close()

    streak = 0
    for (pnl,) in rows:
        if pnl is not None and float(pnl) < 0:
            streak += 1
        else:
            break
    return streak


# ---------------- 交易寫入 ----------------

def insert_trade(symbol, signal, entry_price, size_usdt, stoploss=None, takeprofit=None,
                 leverage=DEFAULT_LEVERAGE, position_value=None, source="MANUAL"):
    """
    建立 OPEN 倉位。若該 symbol 已有 OPEN 倉位,資料庫的 unique index 會擋下來,
    這裡轉成 DuplicateOpenTradeError 讓呼叫端明確處理,而不是靜默寫入第二筆。
    """
    if position_value is None and size_usdt is not None:
        position_value = float(size_usdt) * float(leverage)

    try:
        with transaction() as cur:
            cur.execute(
                """
                INSERT INTO trades (
                    symbol, signal, entry_price, size_usdt, status,
                    stoploss, takeprofit, leverage, position_value, source, opened_at
                )
                VALUES (%s, %s, %s, %s, 'OPEN', %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                RETURNING id;
                """,
                (symbol, signal, entry_price, size_usdt, stoploss, takeprofit,
                 leverage, position_value, source),
            )
            return cur.fetchone()[0]
    except psycopg2.errors.UniqueViolation as exc:
        raise DuplicateOpenTradeError(f"{symbol} 已有 OPEN 倉位") from exc


def close_trade_atomic(symbol, exit_price, close_reason):
    """
    平倉的唯一入口。在單一 transaction 內:
        SELECT ... FOR UPDATE  ->  算損益  ->  UPDATE trades  ->  UPDATE accounts

    回傳已平倉的交易 dict;若該 symbol 當下沒有 OPEN 倉位(例如已被另一條路徑平掉)
    則回傳 None,呼叫端據此判斷「這次沒有平到任何東西」,絕不重複調整餘額。
    """
    exit_price = float(exit_price)

    with transaction() as cur:
        cur.execute(
            """
            SELECT id, signal, entry_price, size_usdt, leverage
              FROM trades
             WHERE symbol = %s AND status = 'OPEN'
             ORDER BY id DESC
             LIMIT 1
               FOR UPDATE;
            """,
            (symbol,),
        )
        row = cur.fetchone()

        if row is None:
            return None

        trade_id, signal, entry_price, size_usdt, leverage = row

        entry_price = _f(entry_price) or 0.0
        size_usdt = _f(size_usdt) or 0.0
        leverage = _f(leverage) or DEFAULT_LEVERAGE

        if entry_price <= 0:
            raise ValueError(f"{symbol} entry_price 異常 ({entry_price}),拒絕平倉以免算出錯誤損益")

        if not is_directional(signal):
            raise ValueError(f"{symbol} 方向無法辨識 ({signal!r}),拒絕平倉")

        change = price_change_pct(signal, entry_price, exit_price)
        roi_pct = change * leverage * 100.0
        pnl_usdt = size_usdt * change * leverage

        cur.execute(
            """
            UPDATE trades
               SET status = 'CLOSED',
                   exit_price = %s,
                   pnl_pct = %s,
                   pnl_usdt = %s,
                   close_reason = %s,
                   pnl_basis = 'LEVERAGED',
                   closed_at = CURRENT_TIMESTAMP
             WHERE id = %s;
            """,
            (exit_price, round(roi_pct, 4), round(pnl_usdt, 4), close_reason, trade_id),
        )

        cur.execute("""
            SELECT balance, wins, losses, trades
              FROM accounts
             ORDER BY id DESC
             LIMIT 1;
        """)
        acc = cur.fetchone()
        balance, wins, losses, trades_count = (
            (float(acc[0]), int(acc[1]), int(acc[2]), int(acc[3])) if acc else (10000.0, 0, 0, 0)
        )

        balance += pnl_usdt
        trades_count += 1
        if pnl_usdt > 0:
            wins += 1
        else:
            losses += 1

        _write_account(cur, balance, wins, losses, trades_count)

        return {
            "id": trade_id,
            "symbol": symbol,
            "signal": signal,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "size_usdt": size_usdt,
            "leverage": leverage,
            "status": "CLOSED",
            "pnl_pct": round(roi_pct, 2),
            "pnl_usdt": round(pnl_usdt, 2),
            "close_reason": close_reason,
            "pnl_basis": "LEVERAGED",
            "account": {
                "balance": round(balance, 2),
                "wins": wins,
                "losses": losses,
                "trades": trades_count,
            },
        }


def close_trade(symbol, exit_price, pnl_pct, pnl_usdt, close_reason):
    """
    向下相容的舊介面。損益一律由 close_trade_atomic 重新計算,
    刻意忽略傳入的 pnl_pct / pnl_usdt —— 呼叫端算出來的值是舊的未乘槓桿版本。
    """
    return close_trade_atomic(symbol, exit_price, close_reason)


def update_trade_stoploss(symbol, stoploss):
    with transaction() as cur:
        cur.execute(
            """
            UPDATE trades
               SET stoploss = %s
             WHERE symbol = %s AND status = 'OPEN';
            """,
            (stoploss, symbol),
        )
    return True
