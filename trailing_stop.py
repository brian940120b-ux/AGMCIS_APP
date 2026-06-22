from database_service import get_open_trades, update_trade_stoploss
from market_data import get_price
from notifier import send_telegram

LEVERAGE = 3

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

        if signal == "做多":
            roi = (price - entry) / entry * 100 * LEVERAGE
            gap = get_gap_percent(roi)
            if gap is None:
                print(symbol, "ROI:", round(roi, 2), "no trailing")
                continue
            new_stoploss = round(price * (1 - gap / 100), 6)
            should_update = new_stoploss > stoploss

        elif signal == "做空":
            roi = (entry - price) / entry * 100 * LEVERAGE
            gap = get_gap_percent(roi)
            if gap is None:
                print(symbol, "ROI:", round(roi, 2), "no trailing")
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

            print("UPDATED:", symbol, stoploss, "->", new_stoploss, "ROI:", roi, "GAP:", gap)
        else:
            print(symbol, "ROI:", roi, "SL unchanged")

if __name__ == "__main__":
    apply_trailing_stop()
