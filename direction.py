"""
向下相容 shim。

方向的實作已移到 agmcis/core/enums.py 的 Direction enum(Phase 1)。
這裡保留原本的函式介面,讓既有呼叫端不用改 —— 但只有一份實作。

新程式碼請直接用:
    from agmcis.core.enums import Direction
"""
from agmcis.core.enums import Direction

LONG = Direction.LONG.value
SHORT = Direction.SHORT.value
WAIT = Direction.WAIT.value


def is_long(signal) -> bool:
    return Direction.parse(signal) is Direction.LONG


def is_short(signal) -> bool:
    return Direction.parse(signal) is Direction.SHORT


def is_directional(signal) -> bool:
    direction = Direction.parse(signal)
    return direction is not None and direction.is_directional


def price_change_pct(signal, entry_price: float, exit_price: float) -> float:
    """回傳價格變動比例(小數),已依方向取正負號。不含槓桿。"""
    direction = Direction.parse(signal)
    if direction is None:
        raise ValueError(f"無法辨識的交易方向: {signal!r}")
    return direction.price_change_pct(entry_price, exit_price)


def _valid_side(signal, entry_price, price, is_stop):
    direction = Direction.parse(signal)
    if direction is None or not direction.is_directional or price is None or entry_price is None:
        return False
    try:
        entry_price = float(entry_price)
        price = float(price)
    except (TypeError, ValueError):
        return False
    if entry_price <= 0 or price <= 0:
        return False

    below = price < entry_price
    if direction is Direction.LONG:
        return below if is_stop else not below
    return (not below) if is_stop else below


def stop_loss_is_valid(signal, entry_price: float, stoploss: float) -> bool:
    """做多的停損必須低於進場價,做空的停損必須高於進場價。"""
    return _valid_side(signal, entry_price, stoploss, is_stop=True)


def take_profit_is_valid(signal, entry_price: float, takeprofit: float) -> bool:
    """做多的停利必須高於進場價,做空的停利必須低於進場價。"""
    return _valid_side(signal, entry_price, takeprofit, is_stop=False)
