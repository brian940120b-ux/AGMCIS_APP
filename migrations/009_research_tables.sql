-- 第六十四節列的其餘表。
--
-- 這一批與前面幾個 migration 的差別:它們**不在下單路徑上**。
-- 交易照樣會發生,只是沒有地方查歷史研究紀錄。所以它們晚做,
-- 而且它們壞掉不該影響交易 —— 讀取端全部要能容忍表不存在。

-- 策略註冊表。生命週期狀態的**權威來源仍然是檔案**
-- (agmcis/strategy/health.py),理由是資料庫掛掉時
-- 「所有策略看起來都是 LIVE」是錯誤的方向。
-- 這張表是給人查的鏡像,不是判斷依據。
CREATE TABLE IF NOT EXISTS strategies (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(60)  NOT NULL UNIQUE,
    status        VARCHAR(20)  NOT NULL DEFAULT 'paper',
    description   TEXT,
    suitable_regimes JSONB,
    needs_candles BOOLEAN NOT NULL DEFAULT FALSE,
    needs_order_book BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 一次回測的設定與結果。
CREATE TABLE IF NOT EXISTS backtests (
    id            SERIAL PRIMARY KEY,
    run_id        VARCHAR(64) NOT NULL UNIQUE,
    strategy      VARCHAR(60),
    symbol        VARCHAR(40),
    timeframe     VARCHAR(10),
    candles       INTEGER,
    -- 完整的成本模型與參數。攤平成欄位的話,改一個參數就要改 schema,
    -- 而舊的回測會被新的 schema 重新詮釋。
    config        JSONB,
    metrics       JSONB,
    verdict       VARCHAR(30),
    -- 合成資料跑出來的結果沒有意義,但它仍然值得留著(用來測管線)。
    -- 這個欄位讓它們不會混進「策略有沒有優勢」的統計。
    synthetic     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_backtests_strategy ON backtests (strategy, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_backtests_created  ON backtests (created_at DESC);

-- 回測底下的分段(Train / Validation / Test / OOS)。
CREATE TABLE IF NOT EXISTS backtest_runs (
    id          SERIAL PRIMARY KEY,
    backtest_id INTEGER NOT NULL REFERENCES backtests(id) ON DELETE CASCADE,
    segment     VARCHAR(20) NOT NULL,
    start_index INTEGER,
    end_index   INTEGER,
    metrics     JSONB,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_backtest_runs_parent ON backtest_runs (backtest_id);

-- 新聞。存下來是為了事後能回答「那天到底發生了什麼」——
-- RSS 來源不保留歷史,當下沒存就永遠拿不回來了。
CREATE TABLE IF NOT EXISTS news (
    id          SERIAL PRIMARY KEY,
    title       TEXT        NOT NULL,
    source      VARCHAR(120),
    url         TEXT,
    sentiment   VARCHAR(20),
    impact      VARCHAR(20),
    score       NUMERIC(10, 4),
    affected_symbols JSONB,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_news_created ON news (created_at DESC);
-- 同一則新聞會在多次輪詢裡重複出現。用標題擋掉。
CREATE UNIQUE INDEX IF NOT EXISTS ux_news_title ON news (md5(title));

-- 系統事件:排程工作的結果、啟動、關閉、例外。
-- 與 audit_logs 的差別是那張表記「誰做了什麼」,這張記「發生了什麼」。
CREATE TABLE IF NOT EXISTS system_events (
    id          SERIAL PRIMARY KEY,
    event_type  VARCHAR(60) NOT NULL,
    severity    VARCHAR(20) NOT NULL DEFAULT 'INFO',
    source      VARCHAR(60),
    detail      TEXT,
    payload     JSONB,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_system_events_created ON system_events (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_system_events_type    ON system_events (event_type, created_at DESC);

-- 非同步任務(第八十五節的 /api/backtest 與 /api/paper)。
--
-- 回測是長時間動作,做成同步 HTTP 端點會變成一個會逾時的請求。
-- 這張表讓它變成「送出 -> 拿到 id -> 之後來查」。
CREATE TABLE IF NOT EXISTS jobs (
    id          SERIAL PRIMARY KEY,
    job_id      VARCHAR(64) NOT NULL UNIQUE,
    kind        VARCHAR(40) NOT NULL,
    -- QUEUED / RUNNING / DONE / FAILED
    status      VARCHAR(20) NOT NULL DEFAULT 'QUEUED',
    params      JSONB,
    result      JSONB,
    error       TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at  TIMESTAMP,
    finished_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_jobs_status  ON jobs (status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_jobs_kind    ON jobs (kind, created_at DESC);
