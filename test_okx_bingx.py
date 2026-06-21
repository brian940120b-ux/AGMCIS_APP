from exchange_universe import get_top_volume_symbols
from market_data import get_ohlcv, get_price

symbols = get_top_volume_symbols(limit=20)

print()
print("========== AGMCIS OKX + BingX Universe ==========")

for i, item in enumerate(symbols, start=1):
    print(
        f"{i}. {item['symbol']} | "
        f"Volume={round(item['volume'], 2)} | "
        f"Exchanges={item['exchanges']}"
    )

print()
print("========== OHLCV Test ==========")

for item in symbols[:5]:
    symbol = item["symbol"]
    df = get_ohlcv(symbol, limit=5)
    price = get_price(symbol)
    print(symbol, "price=", price, "rows=", 0 if df is None else len(df))

print("================================================")
