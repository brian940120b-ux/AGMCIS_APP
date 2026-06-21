from database_service import get_open_trades
from market_data import get_price

LEVERAGE = 3

for t in get_open_trades():
    symbol = t["symbol"]
    signal = t["signal"]
    entry = float(t["entry_price"])
    size = float(t["size_usdt"])
    current = get_price(symbol)

    if current is None:
        print(symbol, "price error")
        continue

    if signal == "做多":
        pnl_pct = (current - entry) / entry * 100
    else:
        pnl_pct = (entry - current) / entry * 100

    roi = pnl_pct * LEVERAGE
    upnl = size * roi / 100

    print(
        symbol,
        signal,
        "Entry=", round(entry, 6),
        "Current=", round(current, 6),
        "Leverage=", f"{LEVERAGE}x",
        "ROI=", round(roi, 2), "%",
        "UPNL=", round(upnl, 2), "USDT"
    )
