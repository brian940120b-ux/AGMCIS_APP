"""
Tier Trailing Stop。

Phase 0.5 的修正:ROI 改用每筆交易的真實槓桿,不再一律當成 3x。
槓桿算錯會直接讓 trailing 的觸發門檻整個偏掉。
"""
from database_service import get_open_trades, update_trade_stoploss
from direction import is_long, is_short
from logger_service import logger
from market_data import get_price
from notifier import send_telegram

def get_gap_percent(roi):
    if roi >= 20:
        return 2
    if roi >= 10:
        return 3
    if roi >= 5:
        return 4
    return None

def apply_trailing_stop():
    trades = get_open_trades()

    for t in trades:
        symbol = t.get("symbol")
        signal = t.get("signal")
        entry = float(t.get("entry_price") or 0)
        stoploss = float(t.get("stoploss") or 0)

        if entry <= 0 or stoploss <= 0:
            continue

        price = get_price(symbol)
        if not price:
            continue

        price = float(price)

        leverage = float(t.get("leverage") or 1)

        if is_long(signal):
            roi = (price - entry) / entry * 100 * leverage
            gap = get_gap_percent(roi)
            if gap is None:
                continue
            new_stoploss = round(price * (1 - gap / 100), 6)
            should_update = new_stoploss > stoploss

        elif is_short(signal):
            roi = (entry - price) / entry * 100 * leverage
            gap = get_gap_percent(roi)
            if gap is None:
                continue
            new_stoploss = round(price * (1 + gap / 100), 6)
            should_update = new_stoploss < stoploss

        else:
            continue

        roi = round(roi, 2)

        if should_update:
            update_trade_stoploss(symbol, new_stoploss)

            send_telegram(
                f"🔁 AGMCIS V52 Tier Trailing Stop\n\n"
                f"幣種：{symbol}\n"
                f"方向：{signal}\n"
                f"現價：{price}\n"
                f"ROI：{roi}%\n"
                f"Trailing Gap：{gap}%\n"
                f"原停損：{stoploss}\n"
                f"新停損：{new_stoploss}"
            )

            logger.info("Trailing Stop | %s | SL %s -> %s | ROI=%s%% gap=%s%%", symbol, stoploss, new_stoploss, roi, gap)

if __name__ == "__main__":
    apply_trailing_stop()
