"""
排程任務定義。

每個 Job 是一個「要做什麼 + 多久做一次」的宣告,不自己管迴圈。
這樣所有任務共用同一個 tick、同一份狀態檔與同一套例外處理,
不會再出現「三個迴圈各自每 60 / 300 / 1800 秒觸發開倉」的情況。
"""
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class Job:
    name: str
    run: Callable[[], object]
    interval_seconds: int
    enabled: bool = True
    # 上次執行的 monotonic 時間。None 代表還沒跑過(啟動後第一個 tick 就會跑)。
    last_run_at: Optional[float] = None
    last_result: object = None
    last_error: Optional[str] = None
    run_count: int = 0
    error_count: int = 0
    tags: list = field(default_factory=list)

    def is_due(self, now=None):
        if not self.enabled:
            return False
        if self.last_run_at is None:
            return True
        now = now if now is not None else time.monotonic()
        return (now - self.last_run_at) >= self.interval_seconds

    def seconds_until_due(self, now=None):
        if not self.enabled:
            return None
        if self.last_run_at is None:
            return 0
        now = now if now is not None else time.monotonic()
        return max(0, self.interval_seconds - (now - self.last_run_at))

    def mark_success(self, result, now=None):
        self.last_run_at = now if now is not None else time.monotonic()
        self.last_result = result
        self.last_error = None
        self.run_count += 1

    def mark_failure(self, error, now=None):
        # 失敗也要更新時間,否則失敗的任務會在每個 tick 重試,把 log 灌爆
        self.last_run_at = now if now is not None else time.monotonic()
        self.last_error = str(error)
        self.error_count += 1

    def status(self):
        return {
            "name": self.name,
            "enabled": self.enabled,
            "interval_seconds": self.interval_seconds,
            "runs": self.run_count,
            "errors": self.error_count,
            "last_error": self.last_error,
        }
