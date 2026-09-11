"""
測試專用的假 ccxt 模組。

因為 exchange_engine.py 在檔案頂端 `import ccxt`,
在還沒有安裝真正 ccxt 套件(或不想在單元測試打真實 API)的情況下,
測試檔會先把這個假模組塞進 sys.modules['ccxt'],
讓 exchange_engine 可以正常 import,同時異常類型跟真正 ccxt 的階層一致
(BaseError -> NetworkError -> RequestTimeout / ExchangeNotAvailable),
確保重試/failover 邏輯測的是「真的會發生在 ccxt 身上」的例外情境。
"""


class BaseError(Exception):
    pass


class NetworkError(BaseError):
    pass


class RequestTimeout(NetworkError):
    pass


class ExchangeNotAvailable(NetworkError):
    pass


class _DummyExchangeClass:
    """僅供 getattr(ccxt, 'bingx') 這類預設 factory 呼叫時不會炸掉,測試中通常會用自訂 factory 取代。"""
    def __init__(self, *args, **kwargs):
        pass


bingx = _DummyExchangeClass
