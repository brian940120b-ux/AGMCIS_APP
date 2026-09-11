"""
Client Order ID。

用途(Master Prompt 第 16 條):
  訂單追蹤、對帳、**防重複下單**、稽核、交易日誌。

防重複是最重要的:送單後如果連線斷掉,我們不知道交易所收到沒有。
帶著同一個 client order id 去查,就能知道那一筆到底存不存在,
而不是盲目重送 —— 重送是重複開倉最常見的來源。

格式:
    AGMCIS-20260911-BTC-000001

⚠️ 長度與允許字元必須符合 BingX 的限制。官方文件在這個環境無法完整查證,
   所以採保守策略:只用英數與連字號,長度上限可由設定調整(預設 32)。
   實際上線前請用 scripts/verify_bingx.py 對真實 API 確認。
"""
import itertools
import re
import threading
from datetime import datetime, timezone

PREFIX = "AGMCIS"
MAX_LENGTH = 32
_SAFE = re.compile(r"[^A-Za-z0-9-]")

_counter = itertools.count(1)
_lock = threading.Lock()


def _base_of(symbol):
    """'BTC/USDT:USDT' -> 'BTC'。去掉所有非英數字元,並截短。"""
    base = str(symbol or "").split("/")[0].split(":")[0]
    return _SAFE.sub("", base.upper())[:6] or "UNK"


def next_sequence():
    with _lock:
        return next(_counter)


def reset_sequence():
    """測試用。"""
    global _counter
    with _lock:
        _counter = itertools.count(1)


def generate(symbol, now=None, sequence=None, max_length=MAX_LENGTH):
    """
    產生一個唯一的 client order id。

    同一個 process 內靠序號保證唯一;跨 process 靠日期 + 序號 + symbol
    降低碰撞機率。真正的全域唯一要等 Phase 16 有共用狀態。
    """
    now = now or datetime.now(timezone.utc)
    sequence = next_sequence() if sequence is None else sequence

    candidate = f"{PREFIX}-{now.strftime('%Y%m%d')}-{_base_of(symbol)}-{sequence:06d}"

    if len(candidate) > max_length:
        # 超長時先犧牲日期的世紀位數,再犧牲 symbol
        candidate = f"{PREFIX}-{now.strftime('%y%m%d')}-{_base_of(symbol)[:3]}-{sequence:06d}"

    return candidate[:max_length]


def is_valid(client_order_id, max_length=MAX_LENGTH):
    if not client_order_id:
        return False
    if len(client_order_id) > max_length:
        return False
    return not _SAFE.search(client_order_id)


def is_ours(client_order_id):
    """對帳時用來分辨「這筆單是不是 AGMCIS 開的」。"""
    return bool(client_order_id) and str(client_order_id).startswith(PREFIX + "-")
