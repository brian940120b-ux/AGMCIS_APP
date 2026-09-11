-- AGMCIS baseline schema
--
-- 這份檔案是把「原本只存在於 VPS Postgres 內、repo 完全沒有版控」的 schema
-- 反推並納入版控。全部語法都是 idempotent,對既有生產資料庫執行不會改動任何資料。
-- 目的是讓環境可以從零重建(dev / test / staging),而不是修改現有生產資料。

CREATE TABLE IF NOT EXISTS accounts (
    id          SERIAL PRIMARY KEY,
    balance     NUMERIC(18, 8) NOT NULL DEFAULT 10000,
    wins        INTEGER        NOT NULL DEFAULT 0,
    losses      INTEGER        NOT NULL DEFAULT 0,
    trades      INTEGER        NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS trades (
    id              SERIAL PRIMARY KEY,
    symbol          VARCHAR(40)  NOT NULL,
    signal          VARCHAR(20)  NOT NULL,
    entry_price     NUMERIC(18, 8),
    exit_price      NUMERIC(18, 8),
    size_usdt       NUMERIC(18, 8),
    status          VARCHAR(20)  NOT NULL DEFAULT 'OPEN',
    pnl_pct         NUMERIC(18, 8),
    pnl_usdt        NUMERIC(18, 8),
    opened_at       TIMESTAMP,
    closed_at       TIMESTAMP,
    stoploss        NUMERIC(18, 8),
    takeprofit      NUMERIC(18, 8),
    close_reason    VARCHAR(100),
    source          VARCHAR(20)  DEFAULT 'MANUAL',
    leverage        NUMERIC(10, 2) DEFAULT 3,
    position_value  NUMERIC(18, 8)
);

CREATE TABLE IF NOT EXISTS trade_journal (
    id          SERIAL PRIMARY KEY,
    symbol      VARCHAR(40) NOT NULL,
    signal      VARCHAR(20),
    action      VARCHAR(20) NOT NULL,
    price       NUMERIC(18, 8),
    reason      TEXT,
    score       NUMERIC(10, 2),
    pnl_usdt    NUMERIC(18, 8),
    pnl_pct     NUMERIC(18, 8),
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 舊環境可能缺這幾個欄位(db_upgrade_v13_4.py 只補了三個),一併補齊。
ALTER TABLE trades ADD COLUMN IF NOT EXISTS stoploss       NUMERIC(18, 8);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS takeprofit     NUMERIC(18, 8);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS close_reason   VARCHAR(100);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS source         VARCHAR(20) DEFAULT 'MANUAL';
ALTER TABLE trades ADD COLUMN IF NOT EXISTS leverage       NUMERIC(10, 2) DEFAULT 3;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS position_value NUMERIC(18, 8);

-- get_open_trades / get_closed_trades 目前是全表掃描,補索引。
CREATE INDEX IF NOT EXISTS ix_trades_status        ON trades (status);
CREATE INDEX IF NOT EXISTS ix_trades_symbol_status ON trades (symbol, status);
CREATE INDEX IF NOT EXISTS ix_journal_created_at   ON trade_journal (created_at DESC);
