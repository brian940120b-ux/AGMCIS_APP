-- Phase 15:績效歸因
--
-- 要回答「哪個 Agent 其實沒有貢獻」「哪種市況下這套系統會虧」,
-- 就必須知道**每一筆交易當時是誰投了什麼票、市況是什麼**。
--
-- 事後推不回來:Agent 的投票取決於當下的指標,而指標會隨時間變。
-- 沒有在開倉當下記下來,那筆交易的歸因就永遠遺失了。
--
-- 既有資料沒有這些欄位,一律留 NULL 並在分析時排除 ——
-- 不是補一個「預設值」進去。補值會讓歸因統計看起來有樣本,
-- 而那些樣本是憑空造的。

ALTER TABLE trades ADD COLUMN IF NOT EXISTS agent_votes    JSONB;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS market_regime  VARCHAR(24);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS strategy       VARCHAR(64);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS confidence     NUMERIC(8, 2);

-- 歸因查詢會依市況與策略分群
CREATE INDEX IF NOT EXISTS ix_trades_regime   ON trades (market_regime);
CREATE INDEX IF NOT EXISTS ix_trades_strategy ON trades (strategy);
