"""
合約中繼資料(Master Prompt 第八節的 contracts.py)。

## 這裡沒有重複實作

第八節說「如果現有專案已有類似模組:不要重複建立。應該:
REFACTOR EXISTING CODE」。

合約規格的快取、校準與過期判斷已經在 `agmcis/exchange/specs.py` ——
那個模組是 Phase 11 為了 BingX 規格校準寫的,而且不只服務 bingx。
這裡只 re-export,並放 bingx 專屬的一件事:**ccxt 的 precision 欄位
怎麼讀**。

## precision 有兩種意思

ccxt 的 `precision.price` 可能是**小數位數**(int,例如 2)也可能是
**最小單位**(float,例如 0.01)。兩者差很多:把 0.01 當成小數位數會
得到 0 位,於是每一張單的價格都被四捨五入成整數。

`decimals()` 把兩種都轉成小數位數。
"""
import math

from agmcis.exchange.specs import (  # noqa: F401  re-export
    ContractSpec,
    SpecSnapshot,
    SpecStore,
    get_store as get_spec_store,
)


def decimals(value):
    """
    ccxt 的 precision 轉成小數位數。看不懂就回 None ——
    回 0 會讓「不知道精度」變成「精度是整數」,而那會讓每一張單
    的價格都被四捨五入掉。
    """
    if value is None:
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if number.is_integer() and number >= 1:
        return int(number)

    if 0 < number < 1:
        # 0.001 -> 3 位小數
        return max(0, round(-1 * math.log10(number)))

    return int(number)


__all__ = ["decimals", "ContractSpec", "SpecSnapshot", "SpecStore", "get_spec_store"]
