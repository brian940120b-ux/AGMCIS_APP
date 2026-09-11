"""
請求節流。

ccxt 的 enableRateLimit 只是「兩次請求之間至少間隔 N 毫秒」的簡單節流,
它不知道我們有多少個 process 在同時打同一把 API Key,
也不會在收到 429 之後自動退讓。

這裡補上兩件事:
  1. Token bucket —— 用「每個時間窗最多幾次請求」來限制,比固定間隔更貼近
     交易所實際的限流方式,而且允許短暫的突發(例如一次掃描 50 檔)。
  2. 收到 429 之後進入冷卻期,期間所有請求都先等待。
     否則被限流時繼續打只會讓封鎖時間變長。

⚠️ 這是行程內的限制器。多個 process(scheduler / web / telegram)共用同一把
   API Key 時,每個 process 各有自己的額度。真正的跨行程限流需要 Redis,
   等 Phase 16 有共用狀態之後再處理。
"""
import threading
import time


class RateLimiter:
    def __init__(self, max_calls=100, period_seconds=10.0, name="bingx", clock=time.monotonic):
        self.max_calls = max_calls
        self.period_seconds = period_seconds
        self.name = name
        self._clock = clock
        self._calls = []
        self._cooldown_until = 0.0
        self._lock = threading.Lock()
        self.throttled_count = 0
        self.cooldown_count = 0

    # ---------------- 查詢 ----------------

    def _prune(self, now):
        cutoff = now - self.period_seconds
        self._calls = [t for t in self._calls if t > cutoff]

    def available(self, now=None):
        now = now if now is not None else self._clock()
        with self._lock:
            self._prune(now)
            return max(0, self.max_calls - len(self._calls))

    def wait_time(self, now=None):
        """還要等多久才能送出下一個請求。0 代表可以馬上送。"""
        now = now if now is not None else self._clock()
        with self._lock:
            return self._wait_time_locked(now)

    def _wait_time_locked(self, now):
        if now < self._cooldown_until:
            return self._cooldown_until - now

        self._prune(now)
        if len(self._calls) < self.max_calls:
            return 0.0

        # 最舊的那一次請求離開時間窗之後就有額度
        return max(0.0, self._calls[0] + self.period_seconds - now)

    @property
    def in_cooldown(self):
        return self._clock() < self._cooldown_until

    # ---------------- 使用 ----------------

    def acquire(self, sleep=time.sleep):
        """
        取得一個額度,必要時等待。回傳實際等待的秒數。
        """
        waited = 0.0

        while True:
            now = self._clock()
            with self._lock:
                delay = self._wait_time_locked(now)
                if delay <= 0:
                    self._calls.append(now)
                    return waited

            self.throttled_count += 1
            sleep(delay)
            waited += delay

    def penalise(self, seconds):
        """
        收到 429 / DDoS 保護時呼叫,進入冷卻期。

        冷卻期間所有請求都會先等待 —— 被限流時繼續打只會讓封鎖時間變長。
        """
        with self._lock:
            now = self._clock()
            self._cooldown_until = max(self._cooldown_until, now + seconds)
            self.cooldown_count += 1
            return self._cooldown_until - now

    def reset(self):
        with self._lock:
            self._calls = []
            self._cooldown_until = 0.0

    def status(self):
        now = self._clock()
        with self._lock:
            self._prune(now)
            return {
                "name": self.name,
                "max_calls": self.max_calls,
                "period_seconds": self.period_seconds,
                "used": len(self._calls),
                "available": max(0, self.max_calls - len(self._calls)),
                "in_cooldown": now < self._cooldown_until,
                "cooldown_remaining": max(0.0, self._cooldown_until - now),
                "throttled_count": self.throttled_count,
                "cooldown_count": self.cooldown_count,
            }
