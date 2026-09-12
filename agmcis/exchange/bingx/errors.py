"""
錯誤分類(Master Prompt 第八節的 errors.py、第十六節)。

## 這裡沒有重複實作

分類與重試策略在 `agmcis/exchange/error_policy.py`,而它不是
bingx 專屬的 —— 「被限流要退更久」「寫入逾時不可重試」這兩條
對任何交易所都成立。

最重要的一條在那個模組裡:**送出訂單後失敗 = ACTION_RECONCILE**。
交易所可能已經收到了,重送是重複開倉最常見的來源,所以那條路徑
拋一個「狀態不明」的例外,由對帳處理,不重試。
"""
from agmcis.exchange.error_policy import (  # noqa: F401
    ACTION_BACKOFF_LONG,
    ACTION_RECONCILE,
    ACTION_RESYNC_TIME,
    classify,
    classify_write_failure,
    describe,
)

__all__ = [
    "classify", "classify_write_failure", "describe",
    "ACTION_BACKOFF_LONG", "ACTION_RECONCILE", "ACTION_RESYNC_TIME",
]
