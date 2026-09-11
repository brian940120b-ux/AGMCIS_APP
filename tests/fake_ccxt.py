"""
測試專用的假 ccxt 模組。

**只有在真的沒有安裝 ccxt 時才會被用到**(見 conftest.py)。
有裝 ccxt 就用真的 —— 錯誤分類是建立在 ccxt 真實的例外階層上,
跑在假階層上會讓測試失去意義。

這裡的階層刻意與真實 ccxt 一致:
    BaseError
      ├─ ExchangeError
      │    ├─ AuthenticationError ─ PermissionDenied
      │    ├─ BadRequest ─ BadSymbol
      │    ├─ InsufficientFunds
      │    ├─ InvalidOrder ─ OrderNotFound
      │    └─ NotSupported
      └─ OperationFailed
           └─ NetworkError
                ├─ RequestTimeout
                ├─ ExchangeNotAvailable ─ OnMaintenance
                ├─ RateLimitExceeded
                ├─ DDoSProtection
                └─ InvalidNonce
"""


class BaseError(Exception):
    pass


class ExchangeError(BaseError):
    pass


class AuthenticationError(ExchangeError):
    pass


class PermissionDenied(AuthenticationError):
    pass


class BadRequest(ExchangeError):
    pass


class BadSymbol(BadRequest):
    pass


class InsufficientFunds(ExchangeError):
    pass


class InvalidOrder(ExchangeError):
    pass


class OrderNotFound(InvalidOrder):
    pass


class NotSupported(ExchangeError):
    pass


class OperationFailed(BaseError):
    pass


class NetworkError(OperationFailed):
    pass


class RequestTimeout(NetworkError):
    pass


class ExchangeNotAvailable(NetworkError):
    pass


class OnMaintenance(ExchangeNotAvailable):
    pass


class RateLimitExceeded(NetworkError):
    pass


class DDoSProtection(NetworkError):
    pass


class InvalidNonce(NetworkError):
    pass


class _DummyExchangeClass:
    """僅供預設 factory 呼叫時不會炸掉。測試中一律用 MagicMock 取代。"""

    def __init__(self, *args, **kwargs):
        self.has = {}
        self.options = {}

    def set_sandbox_mode(self, enabled):
        pass


bingx = _DummyExchangeClass
