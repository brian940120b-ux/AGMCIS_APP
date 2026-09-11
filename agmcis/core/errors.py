"""
例外階層。

原本系統到處用 `except Exception` 或回傳 `{"success": False}` dict,
呼叫端無法區分「使用者輸入錯」「風控擋下」「交易所掛掉」這三種完全不同的情況。
"""


class AgmcisError(Exception):
    """所有 AGMCIS 自訂例外的基底。"""


# ---------------- 設定 ----------------

class ConfigError(AgmcisError):
    """設定缺失或不合法(例如 DB_PASSWORD 未設定)。"""


# ---------------- 交易所 ----------------

class ExchangeError(AgmcisError):
    """交易所相關錯誤的基底。"""


class ExchangeUnavailableError(ExchangeError):
    """重試用盡仍失敗。呼叫端應視為「暫時沒有資料」而不是「資料是這樣」。"""


class OrderRejectedError(ExchangeError):
    """交易所明確拒絕訂單(精度、最小量、保證金不足等)。"""


class OrderStateUnknownError(ExchangeError):
    """
    送單後無法確認結果。

    ⚠️ 遇到這個例外絕對不可以直接重送 —— 必須先查詢交易所並對帳。
    重送是最容易造成重複開倉的路徑。
    """


# ---------------- 資料品質 ----------------

class DataQualityError(AgmcisError):
    """資料不可信(缺 K 棒、離群值、stale)。資料異常時的正確反應是 NO TRADE。"""


# ---------------- 風控與交易規則 ----------------

class RiskRejectedError(AgmcisError):
    """風控擋下。這不是系統錯誤,是系統正常運作的結果。"""

    def __init__(self, reason, blockers=None):
        super().__init__(reason)
        self.reason = reason
        self.blockers = list(blockers or [])


class TradingRuleViolation(AgmcisError):
    """不符合交易所的合約規則(tick size、step size、最小名目等)。"""

    def __init__(self, rule, detail=None):
        super().__init__(f"{rule}: {detail}" if detail else rule)
        self.rule = rule
        self.detail = detail


class DuplicateOpenTradeError(AgmcisError):
    """同一 symbol 已有未平倉位。由資料庫的 unique index 保證。"""


# ---------------- 對帳 ----------------

class ReconciliationMismatch(AgmcisError):
    """內部狀態與交易所實際帳戶不一致。這一定要告警,不能靜默修正。"""

    def __init__(self, symbol, internal, actual):
        super().__init__(f"{symbol} 內部={internal} 交易所={actual}")
        self.symbol = symbol
        self.internal = internal
        self.actual = actual
