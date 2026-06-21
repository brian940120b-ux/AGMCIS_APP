BLOCK_SYMBOLS = [
    "USDC/USDT",
    "FDUSD/USDT",
    "RLUSD/USDT",
    "USD1/USDT",
    "USDG/USDT",
    "TUSD/USDT",
    "EUR/USDT",
    "PAXG/USDT",
    "XAUT/USDT"
]


def filter_symbols(symbols):
    result = []

    for item in symbols:

        symbol = item["symbol"]

        if symbol in BLOCK_SYMBOLS:
            continue

        result.append(item)

    return result
