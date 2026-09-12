-- 決策紀錄(Master Prompt 第六十四 / 六十九 / 七十 / 七十一節)。
--
-- 在這之前,系統可以回答「我現在為什麼要開這一單」,但不能回答
-- 「三週前那一單為什麼開」。決策只寫在 log 與 API 回應裡,而 log 會輪替。
--
-- 第七十一節要求的可解釋性不是「當下說得出來」,是**事後查得到**。
-- 一個只在決策當下存在的理由,對檢討沒有任何用處 —— 而檢討正是
-- 唯一能讓系統變好的東西。

-- 每一次完整的決策。不論最後有沒有下單都要寫 ——
-- 「為什麼沒開」跟「為什麼開」一樣重要,而且發生得頻繁得多。
CREATE TABLE IF NOT EXISTS ai_decisions (
    id              SERIAL PRIMARY KEY,
    decision_id     VARCHAR(64)  NOT NULL UNIQUE,
    symbol          VARCHAR(40)  NOT NULL,
    market_type     VARCHAR(20),
    direction       VARCHAR(20),
    score           NUMERIC(10, 4),
    confidence      NUMERIC(10, 4),
    market_regime   VARCHAR(30),
    volatility      VARCHAR(20),
    -- 最終結果:OPENED / REJECTED_BY_RISK / WAIT / BLOCKED / FAILED
    outcome         VARCHAR(40)  NOT NULL,
    reason          TEXT,
    -- 完整的 Agent 意見、策略裁決、分數細項、風控裁決。
    -- 用 JSONB 而不是攤平成欄位:這些結構會隨著 Agent 增減而改變,
    -- 而歷史紀錄必須保留當時的形狀,不是被新的 schema 重新詮釋。
    agent_votes     JSONB,
    strategy_verdicts JSONB,
    score_breakdown JSONB,
    risk_decision   JSONB,
    news_risk       JSONB,
    -- 下單成功時連到那一筆交易。沒下單就是 NULL。
    trade_id        INTEGER REFERENCES trades(id) ON DELETE SET NULL,
    created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_ai_decisions_symbol  ON ai_decisions (symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_ai_decisions_created ON ai_decisions (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_ai_decisions_outcome ON ai_decisions (outcome, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_ai_decisions_trade   ON ai_decisions (trade_id);

-- 風控事件:被擋下來的、被縮小的、觸發緊急保護的。
-- 第六十五節的稽核要求裡,風控是唯一「每天都在發生但沒有人看得到」的那一類。
CREATE TABLE IF NOT EXISTS risk_events (
    id          SERIAL PRIMARY KEY,
    event_type  VARCHAR(40)  NOT NULL,
    symbol      VARCHAR(40),
    severity    VARCHAR(20)  NOT NULL DEFAULT 'INFO',
    blockers    JSONB,
    detail      TEXT,
    payload     JSONB,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_risk_events_created ON risk_events (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_risk_events_type    ON risk_events (event_type, created_at DESC);

-- 系統稽核(第六十五節):登入、模式切換、策略狀態變更、緊急操作。
--
-- actor 很多時候會是 'system' 或 'env' —— 設定來自環境變數,
-- 環境變數沒有作者。誠實地寫 'env' 比編一個假的使用者名稱好。
CREATE TABLE IF NOT EXISTS audit_logs (
    id          SERIAL PRIMARY KEY,
    action      VARCHAR(60)  NOT NULL,
    actor       VARCHAR(60)  NOT NULL DEFAULT 'system',
    target      VARCHAR(120),
    before_value TEXT,
    after_value  TEXT,
    detail      TEXT,
    source_ip   VARCHAR(60),
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_audit_logs_created ON audit_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_audit_logs_action  ON audit_logs (action, created_at DESC);

-- 市況快照。策略在哪一種市況下賺錢,要有一份獨立於交易的紀錄 ——
-- 只從交易推市況,沒有交易的那些時段就完全看不見了,
-- 而「系統在盤整時不交易」正是我們想確認的事情之一。
CREATE TABLE IF NOT EXISTS market_regimes (
    id          SERIAL PRIMARY KEY,
    symbol      VARCHAR(40)  NOT NULL,
    timeframe   VARCHAR(10)  NOT NULL DEFAULT '1h',
    regime      VARCHAR(30)  NOT NULL,
    volatility  VARCHAR(20),
    adx         NUMERIC(10, 4),
    atr_pct     NUMERIC(10, 4),
    tradeable   BOOLEAN,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_market_regimes_symbol ON market_regimes (symbol, created_at DESC);
