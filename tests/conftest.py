"""
共用測試設定。

把專案根目錄加進 sys.path,並用假的 ccxt 取代真套件 ——
單元測試不連網路、不需要 API Key,也不需要安裝 ccxt。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_ccxt  # noqa: E402

sys.modules.setdefault("ccxt", fake_ccxt)

os.environ.setdefault("EXCHANGE_MAX_RETRIES", "2")
os.environ.setdefault("EXCHANGE_RETRY_BACKOFF_SECONDS", "0")
