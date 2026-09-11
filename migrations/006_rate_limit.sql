-- Phase 16:跨行程限流
--
-- RateLimiter 一直是**行程內**的。scheduler、web、telegram 三個 process
-- 共用同一把 API Key,但每個 process 各有自己的額度 ——
-- 設定 100 次/10 秒,實際上會打到 300 次/10 秒。
--
-- 原本的註解寫「等 Phase 16 有 Redis 之後再處理」。
-- 這裡改用 PostgreSQL:資料庫已經是必要相依,再加一個 Redis 等於多一個
-- 會壞、要監控、要備份的元件,而限流的寫入量完全在 Postgres 的能力內。
--
-- 每次 API 呼叫寫一列。查詢時只算時間窗內的列數。

CREATE TABLE IF NOT EXISTS rate_limit_calls (
    id        BIGSERIAL PRIMARY KEY,
    bucket    VARCHAR(64) NOT NULL,
    called_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 限流查詢永遠是「這個 bucket 在最近 N 秒內有幾列」,這個索引是關鍵路徑。
CREATE INDEX IF NOT EXISTS ix_rate_limit_bucket_time
    ON rate_limit_calls (bucket, called_at DESC);

-- 冷卻期(收到 429 之後)也要跨行程共用 —— 一個 process 被限流了,
-- 其他 process 繼續打只會讓封鎖時間變長。
CREATE TABLE IF NOT EXISTS rate_limit_cooldown (
    bucket     VARCHAR(64) PRIMARY KEY,
    until_at   TIMESTAMPTZ NOT NULL,
    reason     TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
