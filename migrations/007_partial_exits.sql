-- 分批停利(Master Prompt 第五十七節)。
--
-- 一個部位可以分三次平掉。這帶來一個統計上的陷阱:如果每一次分批
-- 都被當成一筆「已平倉交易」,勝率會衝到接近 100% —— 因為你永遠
-- 先收 TP1,而虧的那些還開著。
--
-- 所以分批平倉**不產生新的 trades 列**。它寫進 trade_exits,
-- 而累積的已實現損益記在原本那一列的 realized_partial_usdt。
-- 整筆交易要等最後一部分平掉才算一筆,總損益 = 分批 + 最後。

ALTER TABLE trades ADD COLUMN IF NOT EXISTS tp_stage SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS realized_partial_usdt NUMERIC(18, 8) NOT NULL DEFAULT 0;
-- 建倉當下的名目價值。分批之後 position_value 會變小,
-- 但要算「這筆交易原本多大」以及 R 倍數的時候需要原始值。
ALTER TABLE trades ADD COLUMN IF NOT EXISTS original_position_value NUMERIC(18, 8);

UPDATE trades
   SET original_position_value = position_value
 WHERE original_position_value IS NULL;

CREATE TABLE IF NOT EXISTS trade_exits (
    id              SERIAL PRIMARY KEY,
    trade_id        INTEGER      NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    symbol          VARCHAR(40)  NOT NULL,
    stage           SMALLINT     NOT NULL,
    fraction        NUMERIC(10, 6) NOT NULL,
    exit_price      NUMERIC(18, 8) NOT NULL,
    closed_notional NUMERIC(18, 8) NOT NULL,
    gross_pnl_usdt  NUMERIC(18, 8),
    fee_usdt        NUMERIC(18, 8),
    pnl_usdt        NUMERIC(18, 8),
    reason          VARCHAR(200),
    created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_trade_exits_trade ON trade_exits (trade_id);
CREATE INDEX IF NOT EXISTS ix_trade_exits_symbol ON trade_exits (symbol, created_at DESC);

-- 同一個 stage 只能收一次。沒有這條約束的話,一次重試或一次
-- 排程重疊就會把 TP1 收兩次。
CREATE UNIQUE INDEX IF NOT EXISTS ux_trade_exits_stage
    ON trade_exits (trade_id, stage);

-- 出場計畫的 R 倍數要用**建倉當下**的停損,不是現在的停損。
-- 現在的停損已經被移動過了,用它算 R 倍數會讓目標一路往上飄:
-- 每移動一次停損,TP2 就變遠一點,最後永遠收不到。
ALTER TABLE trades ADD COLUMN IF NOT EXISTS original_stoploss NUMERIC(18, 8);

UPDATE trades
   SET original_stoploss = stoploss
 WHERE original_stoploss IS NULL;
