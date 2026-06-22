from database_service import get_open_trades, update_trade_stoploss
from market_data import get_price
from notifier import send_telegram

TRAILING_ACTIVE_ROI = 5
TRAILING_GAP_PERCENT = 3

def run_trailing_stop():
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
            roi = (price - entry) / entry * 100 * 3
        elif signal == "做空":
            roi = (entry - price) / entry * 100 * 3
        else:
            continue

        roi = round(roi, 2)

        print(symbol, "ROI:", roi, "SL:", stoploss)


if __name__ == "__main__":
    run_trailing_stop()

def preview_trailing_stop():
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
            roi = (price - entry) / entry * 100 * 3
            new_stoploss = round(price * 0.97, 6)
            should_update = roi >= TRAILING_ACTIVE_ROI and new_stoploss > stoploss
        elif signal == "做空":
            roi = (entry - price) / entry * 100 * 3
            new_stoploss = round(price * 1.03, 6)
            should_update = roi >= TRAILING_ACTIVE_ROI and new_stoploss < stoploss
        else:
            continue

        if should_update:
            print("UPDATE CANDIDATE:", symbol, "OLD:", stoploss, "NEW:", new_stoploss, "ROI:", round(roi, 2))

if __name__ == "__main__":
    preview_trailing_stop()

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
            roi = (price - entry) / entry * 100 * 3
            new_stoploss = round(price * 0.97, 6)
            should_update = roi >= TRAILING_ACTIVE_ROI and new_stoploss > stoploss
        elif signal == "做空":
            roi = (entry - price) / entry * 100 * 3
            new_stoploss = round(price * 1.03, 6)
            should_update = roi >= TRAILING_ACTIVE_ROI and new_stoploss < stoploss
        else:
            continue

        if should_update:
            update_trade_stoploss(symbol, new_stoploss)

            send_telegram(
                f"🔁 AGMCIS Trailing Stop Updated\n\n"
                f"幣種：{symbol}\n"
                f"方向：{signal}\n"
                f"現價：{price}\n"
                f"原停損：{stoploss}\n"
                f"新停損：{new_stoploss}\n"
                f"ROI：{round(roi,2)}%"
            )

            print("UPDATED:", symbol, stoploss, "->", new_stoploss)

if __name__ == "__main__":
    apply_trailing_stop()
