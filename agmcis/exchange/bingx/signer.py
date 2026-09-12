"""
BingX 請求簽章(Master Prompt 第八 / 九節)。

## 這裡刻意沒有實作

第八節建議一個 `signer.py`,而這個檔案存在是為了回答一個問題:
**簽章在哪裡做?**

答案是 **ccxt**,而那是一個刻意的選擇,不是偷懶:

第五節寫著「不要靠模型記憶猜 API」。BingX 的簽章規則
(參數排序、時間戳欄位名稱、HMAC 的輸入字串怎麼組)會隨版本變,
而自己手寫一份等於把「猜」寫進程式碼 —— 而且是寫進**送出訂單**
的那一條路徑上。簽章錯的失敗方式還特別難查:錯誤訊息通常是
「Signature verification failed」,看起來像金鑰有問題,
但實際上是參數少了一個或多了一個。

ccxt 是持續對著活的 API 維護的。把它當成 HTTP + 簽章層,
我們專注在它不提供的東西:限流策略、錯誤分類、對時、
合約規則快取、狀態正規化。

## 唯一與簽章有關、而且**必須**由我們負責的事

**時鐘偏移。** BingX 會拒絕時間戳偏差過大的請求,而那個拒絕
看起來也像簽章錯誤。ccxt 不會替你對時,所以那一段在
`client.py` 的 `sync_server_time()` 與 `clock_status()`。

要改成自己簽章的話,先讀 docs/PHASE_3_REPORT.md,
並且**單獨審視過**才能上線 —— 那條路徑會碰真錢。
"""
from agmcis.exchange.bingx import auth

# 簽章由 ccxt 負責。這裡把它明講出來,讓 grep "signer" 的人找得到答案。
SIGNS_REQUESTS = "ccxt"

# 與簽章有關、由我們負責的那一件事。實作在 client.py。
CLOCK_SYNC_OWNER = "agmcis.exchange.bingx.client.BingXClient.sync_server_time"

__all__ = ["SIGNS_REQUESTS", "CLOCK_SYNC_OWNER", "auth"]
