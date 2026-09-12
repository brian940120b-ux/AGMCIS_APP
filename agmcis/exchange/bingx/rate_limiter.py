"""
限流(Master Prompt 第八節的 rate_limiter.py)。

## 這裡沒有重複實作 —— 而且不該有

第八節說「如果現有專案已有類似模組:不要重複建立。應該:
REFACTOR EXISTING CODE」。

限流已經有兩層,而且**都不是 bingx 專屬的**:

    agmcis/exchange/rate_limiter.py       行程內
    agmcis/exchange/shared_rate_limit.py  跨行程(scheduler / web / telegram 共用金鑰)

跨行程那層很重要:三個 process 各自用行程內限流的話,實際請求量
是設定值的三倍,而交易所看到的是同一把金鑰。

在 bingx 底下再放一份實作,會讓「到底哪一個在生效」變成一個要
追程式碼才答得出來的問題 —— 而限流的錯誤只會在被 ban 的時候發現。

組裝在 `client.build_rate_limiter()`。
"""
from agmcis.exchange.rate_limiter import RateLimiter  # noqa: F401
from agmcis.exchange.shared_rate_limit import SharedRateLimiter  # noqa: F401
from agmcis.exchange.bingx.client import build_rate_limiter  # noqa: F401

__all__ = ["RateLimiter", "SharedRateLimiter", "build_rate_limiter"]
