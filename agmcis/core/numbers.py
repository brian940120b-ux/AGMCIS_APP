"""
數字的讀取規則(Master Prompt 第九十四節)。

## 一條規則

**缺資料是 None,不是 0。**

0 是一個會被拿去加總、平均、比較的真實數字。把「沒有量到」寫成 0,
那個 0 之後不會再被質疑 —— 它會安靜地把平均值拉低、把總成本算少、
把「這筆沒資料」變成「這筆是零」。

## 為什麼不是 `row.get(field, 0)`

那個寫法有一個具體的 bug:欄位**存在但值是 None** 的時候,
`.get(key, default)` 回傳的是 None 不是 default —— 預設值永遠用不到。
然後下一行的算術就會拋 TypeError。

而 `row.get(field) or 0` 更糟:它不會炸,它會安靜地把 None **和 0.0**
一起變成 0,連「這裡本來有沒有資料」都問不出來了。

## 這個模組存在的理由

同一段 None-safe 轉型原本散在三個地方(BingX market mixin、
LiveBroker、analytics),而三份就是三種可能不一致的行為。
"""


def as_float(value):
    """None 就是 None。轉不動也是 None,不是 0。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def field_of(row, field):
    """一列資料裡某個欄位的數值。沒有、是 None、或轉不動都回 None。"""
    if not row:
        return None
    return as_float(row.get(field))


def sum_field(rows, field):
    """
    加總一個欄位,並回報**有幾列量不到**。

    回傳 (總和, 缺漏筆數)。

    缺漏筆數是重點。少了它,呼叫端只會拿到一個看起來正常的總和,
    而那個總和少算了幾筆完全看不出來 —— 那正是把 None 當成 0
    最貴的地方:錯誤本身是隱形的。
    """
    total = 0.0
    missing = 0

    for row in rows or []:
        value = field_of(row, field)
        if value is None:
            missing += 1
            continue
        total += value

    return total, missing


def sum_fields(rows, *fields):
    """
    把幾個欄位一起加總(例如進場手續費 + 出場手續費)。

    **任何一個欄位缺值,整列就算缺漏。** 只加得到一半的手續費
    比完全沒有數字更誤導 —— 它看起來像一個完整的答案。
    """
    total = 0.0
    missing = 0

    for row in rows or []:
        values = [field_of(row, field) for field in fields]
        if any(value is None for value in values):
            missing += 1
            continue
        total += sum(values)

    return total, missing
