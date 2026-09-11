"""
交易所錯誤分類與重試策略。

為什麼需要這個:
  原本的重試邏輯是「ccxt.NetworkError -> 重試,ccxt.BaseError -> 放棄」。
  問題是 ccxt 把很多語意完全不同的錯誤都放在 NetworkError 底下:

      RateLimitExceeded -> NetworkError   被限流,用跟逾時一樣的短退避只會繼續撞牆
      DDoSProtection    -> NetworkError   同上,而且更嚴重
      InvalidNonce      -> NetworkError   時鐘偏移造成簽章失敗,重試一百次也沒用,
                                          必須重新同步伺服器時間

  也就是說:被限流跟網路抖動拿到相同待遇,時鐘偏移則會無效重試到用盡次數。

這裡把 ccxt 的例外分成幾個「處理方式不同」的類別,每一類有自己的退避策略。
"""
from dataclasses import dataclass

import ccxt

from agmcis.core.errors import (
    ExchangeUnavailableError,
    OrderRejectedError,
    OrderStateUnknownError,
)

# 動作
ACTION_RETRY = "retry"              # 退避後重試
ACTION_BACKOFF_LONG = "backoff"     # 退避更久後重試(限流)
ACTION_RESYNC_TIME = "resync_time"  # 重新同步伺服器時間後重試一次
ACTION_FAIL = "fail"                # 重試沒有意義,直接失敗
ACTION_RECONCILE = "reconcile"      # 狀態不明,必須查詢交易所再決定,絕不重送


@dataclass(frozen=True)
class ErrorPolicy:
    category: str
    action: str
    # 退避秒數的倍率,會再乘上基礎退避與嘗試次數
    backoff_multiplier: float = 1.0
    exception_class: type = ExchangeUnavailableError
    description: str = ""

    @property
    def is_retryable(self):
        return self.action in (ACTION_RETRY, ACTION_BACKOFF_LONG, ACTION_RESYNC_TIME)


# 順序有意義:先比對的優先。子類別必須排在父類別前面。
_POLICIES = [
    # ---- 限流:退避要明顯長於一般網路錯誤 ----
    (ccxt.DDoSProtection, ErrorPolicy(
        "ddos_protection", ACTION_BACKOFF_LONG, backoff_multiplier=8.0,
        description="觸發交易所的 DDoS 防護,必須大幅退避",
    )),
    (ccxt.RateLimitExceeded, ErrorPolicy(
        "rate_limit", ACTION_BACKOFF_LONG, backoff_multiplier=4.0,
        description="被限流(429),用一般退避只會繼續撞牆",
    )),

    # ---- 時鐘偏移:重試無效,要先對時 ----
    (ccxt.InvalidNonce, ErrorPolicy(
        "clock_skew", ACTION_RESYNC_TIME, backoff_multiplier=0.0,
        description="時間戳被拒,通常是本機時鐘與交易所偏差過大",
    )),

    # ---- 交易所暫時不可用:值得重試 ----
    (ccxt.OnMaintenance, ErrorPolicy(
        "maintenance", ACTION_RETRY, backoff_multiplier=4.0,
        description="交易所維護中",
    )),
    (ccxt.RequestTimeout, ErrorPolicy(
        "timeout", ACTION_RETRY, backoff_multiplier=1.0,
        description="請求逾時",
    )),
    (ccxt.ExchangeNotAvailable, ErrorPolicy(
        "unavailable", ACTION_RETRY, backoff_multiplier=2.0,
        description="交易所暫時不可用",
    )),

    # ---- 明確拒絕:重試沒有意義 ----
    (ccxt.AuthenticationError, ErrorPolicy(
        "auth", ACTION_FAIL, exception_class=ExchangeUnavailableError,
        description="API Key 無效、權限不足或簽章錯誤",
    )),
    (ccxt.InsufficientFunds, ErrorPolicy(
        "insufficient_funds", ACTION_FAIL, exception_class=OrderRejectedError,
        description="保證金不足",
    )),
    (ccxt.OrderNotFound, ErrorPolicy(
        "order_not_found", ACTION_FAIL, exception_class=OrderRejectedError,
        description="找不到該訂單",
    )),
    (ccxt.InvalidOrder, ErrorPolicy(
        "invalid_order", ACTION_FAIL, exception_class=OrderRejectedError,
        description="訂單參數不合法(精度、最小量、最小名目)",
    )),
    (ccxt.BadSymbol, ErrorPolicy(
        "bad_symbol", ACTION_FAIL,
        description="交易對不存在",
    )),
    (ccxt.NotSupported, ErrorPolicy(
        "not_supported", ACTION_FAIL,
        description="這個交易所不支援這個操作",
    )),
    (ccxt.BadRequest, ErrorPolicy(
        "bad_request", ACTION_FAIL,
        description="請求參數不正確",
    )),

    # ---- 其餘 ----
    (ccxt.NetworkError, ErrorPolicy(
        "network", ACTION_RETRY, backoff_multiplier=1.0,
        description="一般網路錯誤",
    )),
    (ccxt.ExchangeError, ErrorPolicy(
        "exchange_error", ACTION_FAIL,
        description="交易所回報的其他錯誤",
    )),
]

DEFAULT_POLICY = ErrorPolicy(
    "unknown", ACTION_FAIL,
    description="未分類的錯誤。保守處理:不重試。",
)


def classify(exc):
    """把一個例外對應到處理策略。"""
    for exception_type, policy in _POLICIES:
        if isinstance(exc, exception_type):
            return policy
    return DEFAULT_POLICY


def classify_write_failure(exc):
    """
    **下單類操作**專用的分類。

    讀取失敗最多就是沒資料;但送出訂單之後失敗完全是另一回事 ——
    逾時代表「交易所可能已經收到、也可能沒有」,這時候重送是最容易
    造成重複開倉的路徑。所以寫入操作的逾時一律轉成 ACTION_RECONCILE:
    先查詢交易所到底收到什麼,再決定。
    """
    policy = classify(exc)

    if policy.category in ("timeout", "network", "unavailable", "maintenance"):
        return ErrorPolicy(
            policy.category, ACTION_RECONCILE,
            exception_class=OrderStateUnknownError,
            description=(
                f"{policy.description};但這是寫入操作,"
                "交易所可能已經收到訂單。必須先查詢對帳,不可重送。"
            ),
        )

    return policy


def describe(exc):
    """給 log 用的一行摘要。"""
    policy = classify(exc)
    return f"{policy.category}/{policy.action}: {type(exc).__name__}: {exc}"
