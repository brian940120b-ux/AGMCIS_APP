"""
跨行程限流的共用狀態(Phase 16)。

問題:`RateLimiter` 是**行程內**的。scheduler、web、telegram 三個 process
共用同一把 API Key,但每個 process 各有自己的額度 ——
設定 100 次 / 10 秒,實際上會打到 300 次 / 10 秒。

原本的註解寫「需要 Redis」。這裡改用 PostgreSQL:

  * 資料庫已經是必要相依。再加一個 Redis 等於多一個會壞、要監控、
    要備份的元件。
  * 限流的寫入量(每次 API 呼叫一列)完全在 Postgres 的能力內。
  * 少一個「只有這一件事需要」的基礎設施。

**共用狀態拿不到時的行為是這個模組最重要的決定。**

兩種選擇:
  (a) 退回行程內限流 —— 系統繼續跑,但限流變鬆
  (b) 擋住所有請求 —— 安全,但資料庫一抖整個系統就停擺

選 (a),而且**大聲記錄**。理由:限流變鬆的後果是被交易所暫時封鎖,
那是可回復的;整個系統停擺會讓已有部位失去監控,那更危險。
但這個降級必須看得見,不能靜靜發生。
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("agmcis.exchange.shared_rate_limit")

# 清掉多久以前的紀錄。只要比最長的時間窗大就夠了。
CLEANUP_OLDER_THAN_SECONDS = 300

# 連續失敗這麼多次就不再嘗試共用狀態(每次失敗都是一次資料庫往返)
MAX_CONSECUTIVE_FAILURES = 3

# 退避多久之後再試一次
RETRY_AFTER_SECONDS = 60.0


class SharedRateLimitStore:
    """
    限流狀態的共用儲存。

    每個方法在失敗時都**拋例外**,由 `SharedRateLimiter` 決定要不要降級 ——
    這一層不自己吞錯誤,否則降級這件事就不會被看見。
    """

    def __init__(self, transaction=None):
        self._transaction = transaction

    @property
    def transaction(self):
        if self._transaction is None:
            from db import transaction
            self._transaction = transaction
        return self._transaction

    def record_call(self, bucket):
        with self.transaction() as cur:
            cur.execute(
                "INSERT INTO rate_limit_calls (bucket) VALUES (%s);", (bucket,),
            )

    def count_calls(self, bucket, period_seconds):
        with self.transaction() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM rate_limit_calls
                 WHERE bucket = %s
                   AND called_at > CURRENT_TIMESTAMP - (%s * INTERVAL '1 second');
                """,
                (bucket, period_seconds),
            )
            return int(cur.fetchone()[0])

    def oldest_call_age(self, bucket, period_seconds):
        """時間窗內最舊那一筆呼叫過了多久。沒有紀錄時回 None。"""
        with self.transaction() as cur:
            cur.execute(
                """
                SELECT EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - MIN(called_at)))
                  FROM rate_limit_calls
                 WHERE bucket = %s
                   AND called_at > CURRENT_TIMESTAMP - (%s * INTERVAL '1 second');
                """,
                (bucket, period_seconds),
            )
            row = cur.fetchone()
            return float(row[0]) if row and row[0] is not None else None

    def set_cooldown(self, bucket, seconds, reason=""):
        until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO rate_limit_cooldown (bucket, until_at, reason)
                VALUES (%s, %s, %s)
                ON CONFLICT (bucket) DO UPDATE SET
                    until_at   = GREATEST(rate_limit_cooldown.until_at, EXCLUDED.until_at),
                    reason     = EXCLUDED.reason,
                    updated_at = CURRENT_TIMESTAMP;
                """,
                (bucket, until, reason),
            )
        return until

    def cooldown_remaining(self, bucket):
        """還要冷卻幾秒。0 代表沒有冷卻中。"""
        with self.transaction() as cur:
            cur.execute(
                """
                SELECT EXTRACT(EPOCH FROM (until_at - CURRENT_TIMESTAMP))
                  FROM rate_limit_cooldown WHERE bucket = %s;
                """,
                (bucket,),
            )
            row = cur.fetchone()

        if not row or row[0] is None:
            return 0.0
        return max(0.0, float(row[0]))

    def cleanup(self, older_than_seconds=CLEANUP_OLDER_THAN_SECONDS):
        with self.transaction() as cur:
            cur.execute(
                """
                DELETE FROM rate_limit_calls
                 WHERE called_at < CURRENT_TIMESTAMP
                     - (%s * INTERVAL '1 second');
                """,
                (older_than_seconds,),
            )


class SharedRateLimiter:
    """
    包在行程內 `RateLimiter` 外面的一層。

    兩層都要通過才放行:共用額度用完要等,行程內額度用完也要等。
    共用狀態拿不到時降級成只用行程內那一層,並且大聲記錄。
    """

    def __init__(self, local, store=None, bucket=None, clock=time.monotonic):
        self.local = local
        self.bucket = bucket or getattr(local, "name", "default")
        self._store = store if store is not None else SharedRateLimitStore()
        self._clock = clock
        self._lock = threading.Lock()

        self._failures = 0
        self._disabled_until = 0.0
        self.degraded_count = 0
        self.last_degrade_reason = None

    # ---- 降級管理 ----

    @property
    def shared_available(self):
        if self._store is None:
            return False
        return self._clock() >= self._disabled_until

    def _degrade(self, operation, exc):
        self._failures += 1
        self.degraded_count += 1
        self.last_degrade_reason = f"{operation}: {type(exc).__name__}: {exc}"

        if self._failures >= MAX_CONSECUTIVE_FAILURES:
            self._disabled_until = self._clock() + RETRY_AFTER_SECONDS
            logger.error(
                "跨行程限流連續失敗 %s 次,暫時降級為行程內限流 %s 秒。"
                "**多個 process 共用同一把 API Key 時,實際請求量會是設定值的倍數。** | %s",
                self._failures, RETRY_AFTER_SECONDS, self.last_degrade_reason,
            )
        else:
            logger.warning(
                "跨行程限流失敗(第 %s 次),本次退回行程內限流 | %s",
                self._failures, self.last_degrade_reason,
            )

    def _recovered(self):
        if self._failures:
            logger.info("跨行程限流恢復正常")
        self._failures = 0
        self._disabled_until = 0.0

    # ---- 主要介面 ----

    def wait_time(self):
        """還要等多久。取兩層之中比較長的那個。"""
        local_wait = self.local.wait_time()

        if not self.shared_available:
            return local_wait

        try:
            with self._lock:
                cooldown = self._store.cooldown_remaining(self.bucket)
                used = self._store.count_calls(self.bucket, self.local.period_seconds)
                oldest_age = (
                    self._store.oldest_call_age(self.bucket, self.local.period_seconds)
                    if used >= self.local.max_calls else None
                )
            self._recovered()
        except Exception as exc:
            self._degrade("wait_time", exc)
            return local_wait

        shared_wait = cooldown

        if used >= self.local.max_calls and oldest_age is not None:
            # 最舊那一筆滾出時間窗之後才有額度
            shared_wait = max(shared_wait, self.local.period_seconds - oldest_age)

        return max(local_wait, max(0.0, shared_wait))

    def acquire(self, sleep=time.sleep):
        """取得一個額度,必要時等待。回傳實際等待的秒數。"""
        waited = 0.0

        while True:
            delay = self.wait_time()
            if delay <= 0:
                break
            sleep(delay)
            waited += delay

        self.local.acquire(sleep=lambda _: None)
        self._record()
        return waited

    def _record(self):
        if not self.shared_available:
            return

        try:
            self._store.record_call(self.bucket)
            self._recovered()
        except Exception as exc:
            self._degrade("record_call", exc)

    def penalise(self, seconds, reason="rate limited"):
        """
        被交易所限流之後進入冷卻。**冷卻必須跨行程共用** ——
        一個 process 被限流了,其他 process 繼續打只會讓封鎖時間變長。
        """
        # 行程內那一層的方法叫 penalise()
        if hasattr(self.local, "penalise"):
            self.local.penalise(seconds)

        if not self.shared_available:
            return

        try:
            self._store.set_cooldown(self.bucket, seconds, reason)
            self._recovered()
        except Exception as exc:
            self._degrade("set_cooldown", exc)

    def status(self):
        status = dict(self.local.status())
        status.update({
            "shared": self.shared_available,
            "bucket": self.bucket,
            "degraded_count": self.degraded_count,
            "last_degrade_reason": self.last_degrade_reason,
        })

        if self.shared_available:
            try:
                status["shared_used"] = self._store.count_calls(
                    self.bucket, self.local.period_seconds,
                )
                status["shared_cooldown"] = round(
                    self._store.cooldown_remaining(self.bucket), 3,
                )
            except Exception as exc:
                status["shared_error"] = f"{type(exc).__name__}: {exc}"

        return status
