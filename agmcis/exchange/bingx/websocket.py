"""
BingX 行情 WebSocket(Master Prompt 第八節的 websocket.py、第四十九節)。

實作在同目錄的 `stream.py`(第四十九節做的時候就在那裡)。
這個檔案是第八節建議的名字,re-export 讓兩個名字都找得到。

最重要的一條在那個模組裡:**過期的報價等於沒有報價**。
一個安靜地永遠重連的背景執行緒,看起來跟正常運作一模一樣,
而使用者會以為自己有即時行情 —— 所以 `get_price()` 過期回 None,
放棄重連時 `/health` 會變 error。
"""
from agmcis.exchange.bingx.stream import (  # noqa: F401
    MarketStream,
    Quote,
    StreamStatus,
    get_stream,
    start_stream,
    stop_stream,
)

__all__ = [
    "MarketStream", "Quote", "StreamStatus",
    "get_stream", "start_stream", "stop_stream",
]
