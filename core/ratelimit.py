"""
限流器 —— Master Prompt 第四十八條 · 2026-09-13

═══ 為什麼現在做 ═══
目前對 BingX 的請求沒有任何節流。平常沒事:每日記帳一天一次,
巡檢十分鐘一次。會出事的是這幾種:

  · `portfolio/universe.py` 掃全交易所的合約,一次幾百個請求
  · `bingx_client._get` 失敗會重試三次 —— 而它**對 429 也照重試**,
    退避只有 0.5s / 1s / 2s。被限流的正確反應是慢下來,
    這個寫法是加速撞牆
  · systemd timer 有好幾支,它們**各跑各的行程**。任何寫在單一
    行程裡的限流器,在這裡等於沒有限流器

第三個是關鍵,也是這一支為什麼要用檔案鎖:
`daily`、`sentinel`、`dashboard`、`gauge` 是四個 process,
它們共用的只有磁碟。

═══ 速率的數字從哪裡來 —— 講清楚 ═══
**它不是從 BingX 文件抄來的。**

第五條說「不要靠模型記憶猜 API」,那條規則在這裡同樣成立:
我沒有辦法在這個環境連到 BingX 的文件,所以我不會編一個數字
再宣稱那是交易所的上限。

預設值是**我方自己設的地板**,不是交易所的天花板:
每秒 5 次、突發 10 次。依據是這套系統實際需要多少 ——
每日記帳 7 個幣、巡檢十分鐘一次、掃池子偶爾幾百個請求,
5 rps 全部都夠用,而它擋得住重試風暴與掃描失控。

接私有端點之前必須拿官方文件核對一次,並把真正的分組限制填進來。
在那之前這個數字的意義是「我們保證不超過這麼快」,
不是「交易所允許這麼快」。

═══ 429 與 418 ═══
  · 429 = 太快了。照 Retry-After 等,**沒有 Retry-After 就退避**,
    而且把令牌桶清空 —— 交易所已經說了我們太快,繼續按原速是裝作沒聽到。
  · 418 = 已經被封。**不重試。** 重試一個封鎖只會延長封鎖。
    直接拋出去,讓呼叫端知道發生了什麼。

═══ 這一支不做什麼 ═══
不做「排隊等到天荒地老」。等超過 max_wait_s 就拋例外 ——
一個會無限期卡住的限流器,會讓記帳腳本掛在那裡而沒有人知道。
超時是一個要被看見的事件(第九十四條)。
"""
from __future__ import annotations

import errno
import fcntl
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from core.logging import get_logger

log = get_logger("ratelimit")

BASE = Path(__file__).resolve().parents[1]
STATE = BASE / "data" / "ratelimit.json"
LOCK = BASE / "data" / "ratelimit.lock"

# 我方自訂的地板(**不是** BingX 公布的上限 —— 見檔頭)。
RATE_PER_S = 5.0
BURST = 10.0

# 等不到令牌就放棄的上限。卡住不是比較安全,卡住是沒有人知道。
MAX_WAIT_S = 20.0

# 被 429 打到之後,沒有 Retry-After 時自己退避多久。
DEFAULT_BACKOFF_S = 2.0


class RateLimited(Exception):
    """交易所說太快了(429)。帶著該等多久。"""

    def __init__(self, message: str, retry_after_s: float | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


class Banned(Exception):
    """已經被封(418)。**不要重試。**"""


class WaitedTooLong(Exception):
    """等令牌等太久。呼叫端要知道這件事,不是安靜地慢下去。"""


@dataclass
class Bucket:
    tokens: float
    updated: float
    blocked_until: float = 0.0

    @classmethod
    def fresh(cls) -> "Bucket":
        return cls(tokens=BURST, updated=time.time())

    def to_dict(self) -> dict:
        return {"tokens": self.tokens, "updated": self.updated,
                "blocked_until": self.blocked_until}

    @classmethod
    def from_dict(cls, d: dict) -> "Bucket":
        try:
            return cls(tokens=float(d["tokens"]),
                       updated=float(d["updated"]),
                       blocked_until=float(d.get("blocked_until") or 0.0))
        except (KeyError, TypeError, ValueError):
            # 狀態檔壞掉 -> 從滿的桶開始,但**出聲**。
            # 悄悄重置等於把「有東西在亂寫這個檔案」藏起來。
            log.warning("限流狀態檔讀不懂,重置令牌桶")
            return cls.fresh()


def _read(handle) -> Bucket:
    handle.seek(0)
    raw = handle.read()
    if not raw.strip():
        return Bucket.fresh()
    try:
        return Bucket.from_dict(json.loads(raw))
    except json.JSONDecodeError:
        log.warning("限流狀態檔不是合法 JSON,重置令牌桶")
        return Bucket.fresh()


def _write(handle, bucket: Bucket) -> None:
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps(bucket.to_dict()))
    handle.flush()
    os.fsync(handle.fileno())


def _refill(bucket: Bucket, now: float) -> None:
    elapsed = max(0.0, now - bucket.updated)
    bucket.tokens = min(BURST, bucket.tokens + elapsed * RATE_PER_S)
    bucket.updated = now


def acquire(weight: float = 1.0, max_wait_s: float = MAX_WAIT_S) -> float:
    """
    取一個令牌。回傳實際等了幾秒(0 代表沒等)。

    跨行程:令牌桶存在檔案裡,用 flock 互斥。四支 systemd timer
    共用同一個桶 —— 這正是重點,不然「限流」只限到自己那一支。

    鎖只在**算數的那一瞬間**持有,不會抱著鎖去 sleep。
    抱著鎖睡覺會讓其他行程一起卡住,那不是限流是互相傷害。
    """
    if weight <= 0:
        return 0.0

    started = time.time()
    LOCK.parent.mkdir(parents=True, exist_ok=True)

    while True:
        with open(LOCK, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                now = time.time()
                bucket = _read(handle)

                # 還在 429 / 418 的冷卻期
                if bucket.blocked_until > now:
                    sleep_for = bucket.blocked_until - now
                else:
                    _refill(bucket, now)
                    if bucket.tokens >= weight:
                        bucket.tokens -= weight
                        _write(handle, bucket)
                        return now - started
                    # 差多少令牌,就要等多久補滿
                    sleep_for = (weight - bucket.tokens) / RATE_PER_S
                    _write(handle, bucket)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        waited = time.time() - started
        if waited + sleep_for > max_wait_s:
            raise WaitedTooLong(
                f"等限流令牌超過 {max_wait_s:.0f} 秒(已等 {waited:.1f} 秒,"
                f"還要 {sleep_for:.1f} 秒)。有東西在灌請求,或冷卻期還沒過。")

        # 加一點抖動:四個行程同時醒來會一起搶,然後一起再睡
        time.sleep(min(sleep_for, max_wait_s) + (os.getpid() % 17) / 1000.0)


def penalise(seconds: float, why: str) -> None:
    """
    交易所說慢下來。把桶清空,並在冷卻期內誰都拿不到令牌。

    **跨行程生效** —— 被限流的是我們這個 IP,不是某一支腳本。
    只讓自己那支慢下來,其他三支照樣衝,等於沒有反應。
    """
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            bucket = _read(handle)
            bucket.tokens = 0.0
            bucket.updated = time.time()
            bucket.blocked_until = max(bucket.blocked_until,
                                       time.time() + max(0.0, seconds))
            _write(handle, bucket)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    log.warning(f"限流冷卻 {seconds:.1f} 秒:{why}")


def _retry_after(headers, default: float = DEFAULT_BACKOFF_S) -> float:
    """
    讀 Retry-After。讀不到就用預設退避 —— **不是 0**。

    交易所已經說了我們太快,「沒告訴我等多久」不等於「可以馬上再來」。
    """
    if headers is None:
        return default
    try:
        raw = (headers.get("Retry-After") if hasattr(headers, "get")
               else None)
        if raw is None:
            return default
        return max(0.0, float(str(raw).strip()))
    except (TypeError, ValueError):
        return default


def check_response(status: int, headers=None, where: str = "") -> None:
    """
    看一眼回應碼,該退就退。

    418 直接拋 Banned 且**不設短冷卻** —— 被封的時候繼續戳只會延長它。
    """
    if status == 429:
        wait = _retry_after(headers)
        penalise(wait, f"429 {where}")
        raise RateLimited(f"BingX 回 429(太快){' ' + where if where else ''}",
                          retry_after_s=wait)
    if status == 418:
        # 封鎖時長交易所不一定會講。冷卻五分鐘只是讓本機不要再自動戳;
        # 真正該做的是人來看一眼發生了什麼。
        penalise(300.0, f"418 {where}")
        raise Banned(
            f"BingX 回 418(已被封){' ' + where if where else ''} —— "
            "不重試。請人工確認是什麼在灌請求。")


def urlopen(url, timeout: float = 15.0, weight: float = 1.0):
    """
    `urllib.request.urlopen` 的限流版。url 收字串或 Request 都行
    —— 原本的呼叫端兩種寫法都有,改用這裡不該還要先統一寫法。

    這個專案有十個地方直接 urlopen BingX。它們全部改走這裡,
    而 `tests/test_ratelimit.py` 會掃 AST 擋住新的繞道 ——
    一個可以繞過的限流器,只是一個讓人放心的裝飾品。
    """
    import urllib.error
    import urllib.request

    where = getattr(url, "full_url", url)
    where = str(where).split("?")[0]

    acquire(weight)
    try:
        return urllib.request.urlopen(url, timeout=timeout)
    except urllib.error.HTTPError as e:
        check_response(e.code, getattr(e, "headers", None), where)
        raise


def requests_get(session, url: str, **kwargs):
    """`requests` 版本。session 給 None 就用 requests 模組本身。"""
    import requests as _requests

    acquire(float(kwargs.pop("weight", 1.0)))
    caller = session or _requests
    resp = caller.get(url, **kwargs)
    check_response(resp.status_code, resp.headers, url)
    return resp


def state() -> dict:
    """給 /health 看的。不取令牌,只看一眼。"""
    try:
        raw = json.loads(LOCK.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return {"tokens": None, "blocked": False, "blocked_for_s": None}
    bucket = Bucket.from_dict(raw) if raw else Bucket.fresh()
    now = time.time()
    _refill(bucket, now)
    remaining = max(0.0, bucket.blocked_until - now)
    return {"tokens": round(bucket.tokens, 2),
            "blocked": remaining > 0,
            "blocked_for_s": round(remaining, 1) if remaining > 0 else None,
            "rate_per_s": RATE_PER_S, "burst": BURST}
