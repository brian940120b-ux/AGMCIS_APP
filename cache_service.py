"""
輕量 TTL 快取

用途:避免每次呼叫都打交易所 API(會撞 rate limit),
同一個 key 在 TTL 秒數內重複呼叫會直接回傳快取值。

目前是行程內記憶體快取(dict)。
之後如果 v1/v2/v3 要跨行程共用快取(例如 Scheduler 跟 Dashboard 是不同 process),
把 CacheService 換成 Redis 實作即可,呼叫端介面(get_or_fetch)不用改。
"""
import time
import threading
from typing import Any, Callable, Dict, Tuple


class CacheService:
    def __init__(self):
        self._store: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str):
        """
        只回傳「還新鮮」的值。過期的值刻意不從 _store 刪除
        —— 它們是 get_or_fetch() 在 API 失敗時的最後防線(見下方 stale fallback),
        會在下次成功 set() 時被自然覆蓋掉,不需要額外清理。
        """
        with self._lock:
            item = self._store.get(key)
            if not item:
                return None
            expires_at, value = item
            if time.time() > expires_at:
                return None
            return value

    def set(self, key: str, value: Any, ttl_seconds: float):
        with self._lock:
            self._store[key] = (time.time() + ttl_seconds, value)

    def get_or_fetch(self, key: str, ttl_seconds: float, fetch_fn: Callable[[], Any]) -> Any:
        """
        先查快取,沒命中才呼叫 fetch_fn() 取新資料並寫回快取。
        fetch_fn 失敗(丟例外)時,如果還有「過期但存在」的舊值,寧可回傳舊值也不要整個掛掉
        —— 這對交易系統很重要:API 短暫抖動不該讓整個 Dashboard 掛掉。
        """
        value = self.get(key)
        if value is not None:
            return value

        try:
            fresh = fetch_fn()
            self.set(key, fresh, ttl_seconds)
            return fresh
        except Exception:
            with self._lock:
                stale = self._store.get(key)
            if stale:
                return stale[1]
            raise

    def invalidate(self, key: str):
        with self._lock:
            self._store.pop(key, None)

    def clear(self):
        with self._lock:
            self._store.clear()


# 全域單例,供各服務共用
cache = CacheService()
