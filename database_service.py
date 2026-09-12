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
from psycopg2.extras import Json

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
    cost_basis,
    agent_votes,
    market_regime,
    strategy,
    confidence,
    original_stoploss,
    tp_stage,
    realized_partial_usdt
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
        # Phase 15 的歸因欄位。舊資料是 None,分析時要排除而不是補預設值。
        "agent_votes": row[25],
        "market_regime": row[26],
        "strategy": row[27],
        "confidence": _f(row[28]),
        # 第五十七節的分批停利。original_stoploss 是建倉當下的停損 ——
        # 出場計畫的 R 倍數必須用它,用現在的停損算會讓目標一路往上飄。
        "original_stoploss": _f(row[29]),
        "tp_stage": int(row[30] or 0),
        "realized_partial_usdt": _f(row[31]) or 0.0,
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
                 liquidation_price=None, cost_basis=None,
                 agent_votes=None, market_regime=None, strategy=None,
                 confidence=None):
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
                    requested_entry_price, entry_fee, liquidation_price, cost_basis,
                    agent_votes, market_regime, strategy, confidence,
                    original_stoploss, original_position_value
                )
                VALUES (%s, %s, %s, %s, 'OPEN', %s, %s, %s, %s, %s, CURRENT_TIMESTAMP,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
                """,
                (symbol, signal, entry_price, size_usdt, stoploss, takeprofit,
                 leverage, position_value, source,
                 requested_entry_price, entry_fee, liquidation_price, cost_basis,
                 Json(agent_votes) if agent_votes else None,
                 market_regime, strategy, confidence,
                 # 建倉當下的停損與名目。停損會被移動、名目會被分批縮小,
                 # 但 R 倍數與「這筆原本多大」都要用原始值算。
                 stoploss, position_value),
            )
            return cur.fetchone()[0]
    except psycopg2.errors.UniqueViolation as exc:
        raise DuplicateOpenTradeError(f"{symbol} 已有 OPEN 倉位") from exc


def reduce_trade_atomic(symbol, fraction, exit_price, stage, reason,
                        costs=None, apply_slippage=True):
    """
    分批平倉(Master Prompt 第五十七節)。在單一 transaction 內把部位
    縮掉 `fraction`,把那一部分的損益結算入帳,原本那一列**繼續是 OPEN**。

    ## 為什麼不產生新的一列

    如果每一次分批都變成一筆「已平倉交易」,勝率會衝到接近 100% ——
    你永遠先收 TP1,而虧的那些還開著。那個數字不是勝率,是
    「分批停利的第一階達成率」,兩者長得一樣但意思完全不同。

    所以:
      * 分批不動 accounts 的 trades / wins / losses,只動 balance。
      * 已實現的部分記在 trades.realized_partial_usdt。
      * 最後一腿平倉時,close_trade_atomic() 會把它加回去,
        整筆交易才算一筆。

    ## 冪等

    stage 由 trade_exits 上的唯一索引擋住。一次重試或一次排程重疊
    不會把 TP1 收兩次 —— 重複的 stage 會回 None(不是例外),
    因為「已經收過了」不是錯誤。

    回傳結算結果 dict;沒有 OPEN 倉位或 stage 已收過時回 None。
    """
    fraction = float(fraction)
    if not 0 < fraction < 1:
        raise ValueError(
            f"分批比例必須介於 0 與 1 之間,收到 {fraction} —— "
            f"要全平請用 close_trade_atomic()"
        )

    exit_price = float(exit_price)
    if exit_price <= 0:
        raise ValueError(f"{symbol} 分批出場價必須大於 0")

    with transaction() as cur:
        cur.execute(
            """
            SELECT id, signal, entry_price, size_usdt, leverage,
                   COALESCE(entry_fee, 0), position_value,
                   EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - COALESCE(opened_at,
                                                                    CURRENT_TIMESTAMP))),
                   COALESCE(realized_partial_usdt, 0),
                   COALESCE(tp_stage, 0),
                   COALESCE(original_position_value, position_value)
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
         entry_fee, position_value, held_seconds,
         realized_partial, current_stage, original_notional) = row

        entry_price = _f(entry_price) or 0.0
        size_usdt = _f(size_usdt) or 0.0
        leverage = _f(leverage) or DEFAULT_LEVERAGE
        entry_fee = _f(entry_fee) or 0.0
        notional = _f(position_value) or (size_usdt * leverage)
        realized_partial = _f(realized_partial) or 0.0
        current_stage = int(current_stage or 0)
        hours_held = max(0.0, (_f(held_seconds) or 0.0) / 3600.0)

        if entry_price <= 0:
            raise ValueError(
                f"{symbol} entry_price 異常 ({entry_price}),拒絕分批平倉"
            )

        if not is_directional(signal):
            raise ValueError(f"{symbol} 方向無法辨識 ({signal!r}),拒絕分批平倉")

        if int(stage) <= current_stage:
            # 已經收過了。這不是錯誤 —— 重試與排程重疊都會走到這裡。
            logger.info(
                "Partial exit skipped | %s | stage %s 已經收過(目前 %s)",
                symbol, stage, current_stage,
            )
            return None

        requested_exit_price = exit_price
        if costs is not None and apply_slippage:
            exit_price = costs.exit_price(exit_price, is_long(signal))

        closed_margin = size_usdt * fraction
        closed_notional = notional * fraction

        change = price_change_pct(signal, entry_price, exit_price)
        gross_pnl = closed_margin * change * leverage

        # 進場手續費按比例分攤到這一腿。剩下的留給後面的腿 ——
        # 全部算在第一腿會讓 TP1 看起來比實際差,TP3 比實際好。
        leg_entry_fee = entry_fee * fraction
        leg_exit_fee = costs.fee(closed_notional) if costs is not None else 0.0
        leg_funding = (
            costs.funding_cost(closed_notional, hours_held, is_long(signal))
            if costs is not None else 0.0
        )

        leg_pnl = gross_pnl - leg_entry_fee - leg_exit_fee - leg_funding

        # 這一腿的虧損不可能超過它自己那一份保證金
        if leg_pnl < -closed_margin:
            leg_pnl = -closed_margin

        cur.execute(
            """
            INSERT INTO trade_exits
                (trade_id, symbol, stage, fraction, exit_price,
                 closed_notional, gross_pnl_usdt, fee_usdt, pnl_usdt, reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (trade_id, stage) DO NOTHING
            RETURNING id;
            """,
            (trade_id, symbol, int(stage), fraction, exit_price,
             round(closed_notional, 8), round(gross_pnl, 4),
             round(leg_entry_fee + leg_exit_fee + leg_funding, 8),
             round(leg_pnl, 4), reason),
        )

        if cur.fetchone() is None:
            # 唯一索引擋下來了 —— 另一條路徑同時收了同一個 stage。
            logger.info(
                "Partial exit skipped | %s | stage %s 已存在", symbol, stage,
            )
            return None

        cur.execute(
            """
            UPDATE trades
               SET size_usdt = %s,
                   position_value = %s,
                   entry_fee = %s,
                   tp_stage = %s,
                   realized_partial_usdt = %s
             WHERE id = %s;
            """,
            (round(size_usdt - closed_margin, 8),
             round(notional - closed_notional, 8),
             round(entry_fee - leg_entry_fee, 8),
             int(stage),
             round(realized_partial + leg_pnl, 4),
             trade_id),
        )

        cur.execute("""
            SELECT balance, wins, losses, trades
              FROM accounts
             ORDER BY id DESC
             LIMIT 1;
        """)
        acc = cur.fetchone()
        balance, wins, losses, trades_count = (
            (float(acc[0]), int(acc[1]), int(acc[2]), int(acc[3]))
            if acc else (10000.0, 0, 0, 0)
        )

        # **只動餘額。** trades / wins / losses 一律不動 —— 見上面的說明。
        balance += leg_pnl
        _write_account(cur, balance, wins, losses, trades_count)

        logger.info(
            "Partial exit | %s | stage %s | 收 %.0f%% @ %s | 損益 %s USDT | %s",
            symbol, stage, fraction * 100, exit_price, round(leg_pnl, 4), reason,
        )

        return {
            "id": trade_id,
            "symbol": symbol,
            "signal": signal,
            "stage": int(stage),
            "fraction": fraction,
            "exit_price": exit_price,
            "requested_exit_price": requested_exit_price,
            "closed_notional": round(closed_notional, 8),
            "closed_margin": round(closed_margin, 8),
            "gross_pnl_usdt": round(gross_pnl, 4),
            "pnl_usdt": round(leg_pnl, 4),
            "fee_usdt": round(leg_entry_fee + leg_exit_fee, 8),
            "funding_usdt": round(leg_funding, 8),
            "remaining_size_usdt": round(size_usdt - closed_margin, 8),
            "remaining_notional": round(notional - closed_notional, 8),
            "realized_partial_usdt": round(realized_partial + leg_pnl, 4),
            "reason": reason,
            "account": {
                "balance": round(balance, 2),
                "wins": wins,
                "losses": losses,
                "trades": trades_count,
            },
        }


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
                                                                    CURRENT_TIMESTAMP))),
                   COALESCE(realized_partial_usdt, 0),
                   COALESCE(original_position_value, position_value)
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
         entry_fee, position_value, held_seconds,
         realized_partial, original_notional) = row

        realized_partial = _f(realized_partial) or 0.0

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

        # 分批停利已經實現的部分要算進這一筆交易的總損益。
        # 不加進來的話,一筆「TP1 收了 +80、剩下的虧 -20」會被記成 -20,
        # 而它其實是一筆賺 60 的交易 —— 勝率與期望值都會被記反。
        final_leg_pnl = pnl_usdt
        pnl_usdt = pnl_usdt + realized_partial

        # ROI 的分母是**原始**保證金,不是剩下那一部分的保證金。
        # 用剩餘保證金當分母,分批收得越多 ROI 看起來越誇張。
        original_notional = _f(original_notional) or (size_usdt * leverage)
        original_margin = (
            original_notional / leverage if leverage else size_usdt
        )
        roi_pct = (pnl_usdt / original_margin * 100.0) if original_margin else 0.0

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

        # 餘額只加**這一腿**的損益 —— 分批的部分在當時就已經入帳了。
        balance += final_leg_pnl
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


# ---------------- 決策紀錄(第六十九 / 七十 / 七十一節)----------------
#
# 這幾個寫入函式**不吞例外** —— 呼叫端(agmcis/review/decision_log.py)
# 負責決定失敗要不要擋住交易,而它的答案是「不要」。把決定寫在那一層,
# 這一層保持誠實。

def insert_ai_decision(payload):
    """寫一筆決策紀錄。回傳 id。"""
    with transaction() as cur:
        cur.execute(
            """
            INSERT INTO ai_decisions (
                decision_id, symbol, market_type, direction, score, confidence,
                market_regime, volatility, outcome, reason,
                agent_votes, strategy_verdicts, score_breakdown,
                risk_decision, news_risk, trade_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s)
            ON CONFLICT (decision_id) DO NOTHING
            RETURNING id;
            """,
            (
                payload["decision_id"], payload["symbol"], payload.get("market_type"),
                payload.get("direction"), payload.get("score"),
                payload.get("confidence"), payload.get("market_regime"),
                payload.get("volatility"), payload["outcome"], payload.get("reason"),
                Json(payload.get("agent_votes")) if payload.get("agent_votes") else None,
                Json(payload.get("strategy_verdicts")) if payload.get("strategy_verdicts") else None,
                Json(payload.get("score_breakdown")) if payload.get("score_breakdown") else None,
                Json(payload.get("risk_decision")) if payload.get("risk_decision") else None,
                Json(payload.get("news_risk")) if payload.get("news_risk") else None,
                payload.get("trade_id"),
            ),
        )
        row = cur.fetchone()
        return row[0] if row else None


def link_decision_to_trade(decision_id, trade_id):
    """
    下單成功之後才知道 trade_id。分成兩步寫,是因為決策紀錄必須在
    **下單之前**就存在 —— 下單當下當機的話,那筆決策不能跟著消失。
    """
    with transaction() as cur:
        cur.execute(
            "UPDATE ai_decisions SET trade_id = %s WHERE decision_id = %s;",
            (trade_id, decision_id),
        )
    return True


def insert_risk_event(payload):
    with transaction() as cur:
        cur.execute(
            """
            INSERT INTO risk_events
                (event_type, symbol, severity, blockers, detail, payload)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (
                payload["event_type"], payload.get("symbol"),
                payload.get("severity", "INFO"),
                Json(payload.get("blockers")) if payload.get("blockers") else None,
                payload.get("detail"),
                Json(payload.get("payload")) if payload.get("payload") else None,
            ),
        )
        return cur.fetchone()[0]


def insert_audit_log(payload):
    with transaction() as cur:
        cur.execute(
            """
            INSERT INTO audit_logs
                (action, actor, target, before_value, after_value, detail, source_ip)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (
                payload["action"], payload.get("actor", "system"),
                payload.get("target"), payload.get("before_value"),
                payload.get("after_value"), payload.get("detail"),
                payload.get("source_ip"),
            ),
        )
        return cur.fetchone()[0]


def insert_market_regime(payload):
    with transaction() as cur:
        cur.execute(
            """
            INSERT INTO market_regimes
                (symbol, timeframe, regime, volatility, adx, atr_pct, tradeable)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id;
            """,
            (
                payload["symbol"], payload.get("timeframe", "1h"),
                payload["regime"], payload.get("volatility"),
                payload.get("adx"), payload.get("atr_pct"),
                payload.get("tradeable"),
            ),
        )
        return cur.fetchone()[0]


DECISION_COLUMNS = """
    decision_id, symbol, market_type, direction, score, confidence,
    market_regime, volatility, outcome, reason,
    agent_votes, strategy_verdicts, score_breakdown,
    risk_decision, news_risk, trade_id, created_at
"""


def _row_to_decision(row):
    return {
        "decision_id": row[0],
        "symbol": row[1],
        "market_type": row[2],
        "direction": row[3],
        "score": _f(row[4]),
        "confidence": _f(row[5]),
        "market_regime": row[6],
        "volatility": row[7],
        "outcome": row[8],
        "reason": row[9],
        "agent_votes": row[10],
        "strategy_verdicts": row[11],
        "score_breakdown": row[12],
        "risk_decision": row[13],
        "news_risk": row[14],
        "trade_id": row[15],
        "created_at": row[16].strftime("%Y-%m-%d %H:%M:%S") if row[16] else None,
    }


def get_decision_for_trade(trade_id):
    """「為什麼你開這一單?」"""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {DECISION_COLUMNS} FROM ai_decisions "
            f"WHERE trade_id = %s ORDER BY id DESC LIMIT 1;",
            (trade_id,),
        )
        row = cur.fetchone()
    return _row_to_decision(row) if row else None


def get_decisions(limit=50, symbol=None, outcome=None):
    conn = get_connection()
    clauses, params = [], []

    if symbol:
        clauses.append("symbol = %s")
        params.append(symbol)
    if outcome:
        clauses.append("outcome = %s")
        params.append(outcome)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(int(limit))

    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {DECISION_COLUMNS} FROM ai_decisions {where} "
            f"ORDER BY id DESC LIMIT %s;",
            tuple(params),
        )
        rows = cur.fetchall()

    return [_row_to_decision(row) for row in rows]


def get_risk_events(limit=50):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT event_type, symbol, severity, blockers, detail, created_at "
            "FROM risk_events ORDER BY id DESC LIMIT %s;",
            (int(limit),),
        )
        rows = cur.fetchall()

    return [{
        "event_type": row[0], "symbol": row[1], "severity": row[2],
        "blockers": row[3], "detail": row[4],
        "created_at": row[5].strftime("%Y-%m-%d %H:%M:%S") if row[5] else None,
    } for row in rows]


def get_audit_logs(limit=50):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT action, actor, target, before_value, after_value, "
            "detail, created_at FROM audit_logs ORDER BY id DESC LIMIT %s;",
            (int(limit),),
        )
        rows = cur.fetchall()

    return [{
        "action": row[0], "actor": row[1], "target": row[2],
        "before_value": row[3], "after_value": row[4], "detail": row[5],
        "created_at": row[6].strftime("%Y-%m-%d %H:%M:%S") if row[6] else None,
    } for row in rows]
