"""
共用測試設定。

把專案根目錄加進 sys.path。

關於 ccxt:
  這些測試**不連網路** —— 交易所實體一律用 MagicMock 注入,
  所以不需要把 ccxt 換掉也不會打到真實 API。

  而且錯誤分類(agmcis/exchange/error_policy.py)是建立在 ccxt 真實的
  例外階層上的(例如 RateLimitExceeded 與 InvalidNonce 都是 NetworkError 的子類)。
  如果測試跑在假的階層上,ccxt 哪天改了階層,production 會壞掉但測試還是綠的。
  所以真的有裝 ccxt 就用真的,沒裝才退回 fake_ccxt。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import ccxt  # noqa: F401
except ImportError:
    import fake_ccxt

    sys.modules["ccxt"] = fake_ccxt

os.environ.setdefault("EXCHANGE_MAX_RETRIES", "2")
os.environ.setdefault("EXCHANGE_RETRY_BACKOFF_SECONDS", "0")
