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
from direction import price_change_pct, is_directional, is_long

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
    pnl_basis,
    liquidation_price,
    entry_fee,
    exit_fee,
    funding_usdt,
    gross_pnl_usdt,
    requested_entry_price,
    cost_basis
"""

# ⚠️ 這份欄位清單與 _row_to_trade() 的索引是綁死的。
# Phase 0.5 抓到過一次:leverage 沒有被 SELECT 出來,於是每一筆交易
# 不管實際槓桿多少都變成預設的 3x。加欄位時兩邊都要改。


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
        # Phase 10 的成本欄位。position_monitor 需要 liquidation_price
        # 才判斷得出強平 —— 漏掉它強平就永遠不會觸發。
        "liquidation_price": _f(row[18]),
        "entry_fee": _f(row[19]),
        "exit_fee": _f(row[20]),
        "funding_usdt": _f(row[21]),
        "gross_pnl_usdt": _f(row[22]),
        "requested_entry_price": _f(row[23]),
        "cost_basis": row[24],
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
                 leverage=DEFAULT_LEVERAGE, position_value=None, source="MANUAL",
                 requested_entry_price=None, entry_fee=None,
                 liquidation_price=None, cost_basis=None):
    """
    建立 OPEN 倉位。若該 symbol 已有 OPEN 倉位,資料庫的 unique index 會擋下來,
    這裡轉成 DuplicateOpenTradeError 讓呼叫端明確處理,而不是靜默寫入第二筆。

    Phase 10 起 entry_price 是**實際成交價**(含點差與滑點),
    requested_entry_price 保留下單當下看到的價格。兩者的差就是進場滑價,
    分開存才看得出來成本跑到哪裡去了。
    """
    if position_value is None and size_usdt is not None:
        position_value = float(size_usdt) * float(leverage)

    try:
        with transaction() as cur:
            cur.execute(
                """
                INSERT INTO trades (
                    symbol, signal, entry_price, size_usdt, status,
                    stoploss, takeprofit, leverage, position_value, source, opened_at,
                    requested_entry_price, entry_fee, liquidation_price, cost_basis
                )
                VALUES (%s, %s, %s, %s, 'OPEN', %s, %s, %s, %s, %s, CURRENT_TIMESTAMP,
                        %s, %s, %s, %s)
                RETURNING id;
                """,
                (symbol, signal, entry_price, size_usdt, stoploss, takeprofit,
                 leverage, position_value, source,
                 requested_entry_price, entry_fee, liquidation_price, cost_basis),
            )
            return cur.fetchone()[0]
    except psycopg2.errors.UniqueViolation as exc:
        raise DuplicateOpenTradeError(f"{symbol} 已有 OPEN 倉位") from exc


def close_trade_atomic(symbol, exit_price, close_reason, costs=None,
                       liquidated=False, apply_slippage=True):
    """
    平倉的唯一入口。在單一 transaction 內:
        SELECT ... FOR UPDATE  ->  算損益  ->  UPDATE trades  ->  UPDATE accounts

    回傳已平倉的交易 dict;若該 symbol 當下沒有 OPEN 倉位(例如已被另一條路徑平掉)
    則回傳 None,呼叫端據此判斷「這次沒有平到任何東西」,絕不重複調整餘額。

    Phase 10:損益扣掉成本。

        net = gross - 進場手續費 - 出場手續費 - 資金費用 [- 清算費]

    成本在平倉時一次結算,不是在發生的當下逐筆扣。總額正確,時點簡化 ——
    對「這個策略扣掉成本還剩多少」這個問題沒有影響。

    傳進來的 exit_price 是「看到的價格」。實際成交價在這個 transaction 裡面算 ——
    方向是從已鎖定的那一列讀出來的,不是再查一次資料庫。
    再查一次既多一次往返,方向也可能在兩次讀之間被改掉。

    liquidated=True 時不套用滑點:強平是在強平價成交的,
    再加一次滑點等於把同一件事算兩遍。

    costs 給 None 時**完全不扣成本**,行為與 Phase 10 之前相同。
    呼叫端一律應該帶成本進來;留這條路只是為了讓純粹算數的測試能單獨驗證損益公式。
    """
    exit_price = float(exit_price)

    with transaction() as cur:
        cur.execute(
            """
            SELECT id, signal, entry_price, size_usdt, leverage,
                   COALESCE(entry_fee, 0), position_value,
                   EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - COALESCE(opened_at,
                                                                    CURRENT_TIMESTAMP)))
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

        (trade_id, signal, entry_price, size_usdt, leverage,
         entry_fee, position_value, held_seconds) = row

        entry_price = _f(entry_price) or 0.0
        size_usdt = _f(size_usdt) or 0.0
        leverage = _f(leverage) or DEFAULT_LEVERAGE
        entry_fee = _f(entry_fee) or 0.0
        notional = _f(position_value) or (size_usdt * leverage)
        hours_held = max(0.0, (_f(held_seconds) or 0.0) / 3600.0)

        if entry_price <= 0:
            raise ValueError(f"{symbol} entry_price 異常 ({entry_price}),拒絕平倉以免算出錯誤損益")

        if not is_directional(signal):
            raise ValueError(f"{symbol} 方向無法辨識 ({signal!r}),拒絕平倉")

        requested_exit_price = exit_price
        if costs is not None and apply_slippage and not liquidated:
            exit_price = costs.exit_price(exit_price, is_long(signal))

        change = price_change_pct(signal, entry_price, exit_price)
        gross_pnl = size_usdt * change * leverage

        exit_fee = 0.0
        funding = 0.0

        if costs is not None:
            exit_fee = costs.fee(notional)
            if liquidated:
                exit_fee += costs.liquidation_cost(notional)
            funding = costs.funding_cost(notional, hours_held, is_long(signal))

        pnl_usdt = gross_pnl - entry_fee - exit_fee - funding

        # 虧損不可能超過保證金 —— 強平就是為了這件事
        if pnl_usdt < -size_usdt:
            pnl_usdt = -size_usdt

        roi_pct = (pnl_usdt / size_usdt * 100.0) if size_usdt else 0.0

        cur.execute(
            """
            UPDATE trades
               SET status = 'CLOSED',
                   exit_price = %s,
                   requested_exit_price = %s,
                   pnl_pct = %s,
                   pnl_usdt = %s,
                   gross_pnl_usdt = %s,
                   exit_fee = %s,
                   funding_usdt = %s,
                   close_reason = %s,
                   pnl_basis = 'LEVERAGED',
                   cost_basis = %s,
                   closed_at = CURRENT_TIMESTAMP
             WHERE id = %s;
            """,
            (exit_price, requested_exit_price, round(roi_pct, 4),
             round(pnl_usdt, 4), round(gross_pnl, 4), round(exit_fee, 8),
             round(funding, 8), close_reason,
             "WITH_COSTS" if costs is not None else "NO_COSTS", trade_id),
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
            "gross_pnl_usdt": round(gross_pnl, 4),
            "entry_fee": round(entry_fee, 6),
            "exit_fee": round(exit_fee, 6),
            "funding_usdt": round(funding, 6),
            "hours_held": round(hours_held, 4),
            "liquidated": bool(liquidated),
            "close_reason": close_reason,
            "pnl_basis": "LEVERAGED",
            "cost_basis": "WITH_COSTS" if costs is not None else "NO_COSTS",
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
