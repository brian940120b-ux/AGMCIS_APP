"""
交易方向的單一定義來源。

原本方向用中文字串 "做多" / "做空" 散落在 12 個以上模組裡直接比對,
沒有任何保護 —— 打錯一個字就會靜默變成「不做多也不做空」而 PnL 算成 0。
這裡集中處理,並同時接受中文與英文寫法,讓舊資料與舊呼叫端都能正常運作。
"""

LONG = "做多"
SHORT = "做空"
WAIT = "觀望"

_LONG_ALIASES = {LONG, "LONG", "BUY", "多", "long", "buy"}
_SHORT_ALIASES = {SHORT, "SHORT", "SELL", "空", "short", "sell"}


def is_long(signal) -> bool:
    return signal in _LONG_ALIASES


def is_short(signal) -> bool:
    return signal in _SHORT_ALIASES


def is_directional(signal) -> bool:
    """只有明確的多或空才算可交易方向;其餘(觀望 / None / 未知字串)都不是。"""
    return is_long(signal) or is_short(signal)


def price_change_pct(signal, entry_price: float, exit_price: float) -> float:
    """
    回傳「標的價格變動比例」(小數,非百分比),已依方向取正負號。
    這一層不含槓桿 —— 槓桿只在換算 ROI 與 USDT 損益時才乘上去。
    """
    entry_price = float(entry_price)
    exit_price = float(exit_price)

    if entry_price <= 0:
        raise ValueError(f"entry_price 必須大於 0,收到 {entry_price}")

    if is_long(signal):
        return (exit_price - entry_price) / entry_price
    if is_short(signal):
        return (entry_price - exit_price) / entry_price

    raise ValueError(f"無法辨識的交易方向: {signal!r}")


def stop_loss_is_valid(signal, entry_price: float, stoploss: float) -> bool:
    """做多的停損必須低於進場價,做空的停損必須高於進場價。"""
    if stoploss is None or entry_price is None:
        return False
    try:
        entry_price = float(entry_price)
        stoploss = float(stoploss)
    except (TypeError, ValueError):
        return False
    if entry_price <= 0 or stoploss <= 0:
        return False
    if is_long(signal):
        return stoploss < entry_price
    if is_short(signal):
        return stoploss > entry_price
    return False


def take_profit_is_valid(signal, entry_price: float, takeprofit: float) -> bool:
    """做多的停利必須高於進場價,做空的停利必須低於進場價。"""
    if takeprofit is None or entry_price is None:
        return False
    try:
        entry_price = float(entry_price)
        takeprofit = float(takeprofit)
    except (TypeError, ValueError):
        return False
    if entry_price <= 0 or takeprofit <= 0:
        return False
    if is_long(signal):
        return takeprofit > entry_price
    if is_short(signal):
        return takeprofit < entry_price
    return False
