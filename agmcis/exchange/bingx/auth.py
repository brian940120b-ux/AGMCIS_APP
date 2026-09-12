"""
BingX 憑證(Master Prompt 第八 / 九 / 十 / 八十四節)。

這一層只做一件事:**把金鑰交給 ccxt,而且不讓它出現在任何其他地方。**

## 為什麼值得一個獨立的檔案

金鑰洩漏不是一個「小心一點就好」的問題,是一個要靠結構防的問題。
散在建構函式裡的時候,任何人加一行 debug log 都可能把它印出來,
而那一行會通過 code review,因為它看起來只是在印設定。

把它收斂到這裡之後,規則只有一條而且看得見:
**這個模組之外的任何地方都不該碰 EXCHANGE_CREDENTIALS。**
`tests/test_bingx_modules.py` 有一條測試在掃這件事。

## 第十 / 八十四節

金鑰不得出現在原始碼、Git、Log、例外訊息、資料庫、前端、瀏覽器、
Telegram 或 AI Prompt。這裡提供 `fingerprint()` 給需要「確認是哪一把
金鑰」的地方用 —— 它只回後四碼,不足以還原金鑰。

提款權限必須是關的。那一項由 `scripts/preflight.py` 檢查,
不在這裡 —— 這一層看不到權限,它只看得到字串。
"""
import logging

from agmcis.config import settings

logger = logging.getLogger("agmcis.exchange.bingx.auth")


def credentials():
    """
    要交給 ccxt 的憑證。**空值一律不放進去** ——
    傳一個空字串的 apiKey 會讓 ccxt 以為有金鑰,然後在簽章時失敗,
    而錯誤訊息看起來像金鑰無效而不是金鑰沒設定。
    """
    return {
        key: value
        for key, value in settings.EXCHANGE_CREDENTIALS.items()
        if value
    }


def has_credentials():
    return bool(settings.has_exchange_credentials())


def fingerprint():
    """
    金鑰的後四碼,給「確認是哪一把」用。

    沒有金鑰時回 None —— 不回空字串,那會讓「沒有金鑰」與
    「金鑰是空的」看起來一樣。
    """
    key = str(settings.EXCHANGE_CREDENTIALS.get("apiKey") or "")
    return key[-4:] if len(key) >= 4 else None


def describe():
    """
    給 log 與健康檢查用的一行描述。**永遠不含金鑰本體。**
    """
    if not has_credentials():
        return "no credentials (public data only)"

    tail = fingerprint()
    mode = "testnet (VST)" if settings.EXCHANGE_USE_TESTNET else "live"
    return f"key ...{tail} / {mode}"
