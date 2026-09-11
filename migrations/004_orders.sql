-- Phase 13:訂單持久化
--
-- Phase 12 的 Order 只活在記憶體裡。程式重啟時 UNKNOWN 狀態的訂單就消失了 ——
-- 而那正是**最需要被記住**的狀態:系統不知道交易所收到了什麼,
-- 忘記它等於放棄對帳,而放棄對帳之後任何重試都可能開出重複倉位。
--
-- client_order_id 是唯一鍵。它由我們產生、送給交易所,
-- 對帳時就是靠它把「我們以為送出去的」和「交易所那邊有的」對起來。

CREATE TABLE IF NOT EXISTS orders (
    id                  SERIAL PRIMARY KEY,
    client_order_id     VARCHAR(64)  NOT NULL,
    exchange_order_id   VARCHAR(64),
    symbol              VARCHAR(40)  NOT NULL,
    market_type         VARCHAR(20)  NOT NULL DEFAULT 'perpetual',
    side                VARCHAR(10)  NOT NULL,
    order_type          VARCHAR(24)  NOT NULL,
    state               VARCHAR(24)  NOT NULL,
    quantity            NUMERIC(24, 12),
    filled_quantity     NUMERIC(24, 12) DEFAULT 0,
    price               NUMERIC(18, 8),
    average_fill_price  NUMERIC(18, 8),
    reduce_only         BOOLEAN      DEFAULT FALSE,
    reject_reason       TEXT,
    trade_id            INTEGER,
    source              VARCHAR(20)  DEFAULT 'EXEC',
    created_at          TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    reconciled_at       TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_client_order_id
    ON orders (client_order_id);

-- 對帳只關心「還沒結束」與「狀態不明」的訂單,這兩個查詢要快。
CREATE INDEX IF NOT EXISTS ix_orders_state         ON orders (state);
CREATE INDEX IF NOT EXISTS ix_orders_symbol_state  ON orders (symbol, state);

-- 狀態轉移的稽核軌跡。出事之後要回答「那張單當時到底走到哪一步」,
-- 只有最終狀態是不夠的。
CREATE TABLE IF NOT EXISTS order_events (
    id              SERIAL PRIMARY KEY,
    client_order_id VARCHAR(64) NOT NULL,
    from_state      VARCHAR(24),
    to_state        VARCHAR(24) NOT NULL,
    reason          TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_order_events_coid
    ON order_events (client_order_id, id);
