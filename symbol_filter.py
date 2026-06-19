BLOCK_SYMBOLS = [
    "USDC/USDT",
    "FDUSD/USDT",
    "RLUSD/USDT",
    "USD1/USDT",
    "USDG/USDT",
    "TUSD/USDT",
    "USDP/USDT",
    "DAI/USDT",
    "EUR/USDT",
    "PAXG/USDT",
    "XAUT/USDT"
]

BLOCK_KEYWORDS = [
    "UP",
    "DOWN",
    "BULL",
    "BEAR",
    "3L",
    "3S",
    "5L",
    "5S"
]


def filter_symbols(symbols):
    result = []

    for item in symbols:
        symbol = item["symbol"]
        base = symbol.split("/")[0]

        if symbol in BLOCK_SYMBOLS:
            continue

        blocked = False

        for word in BLOCK_KEYWORDS:
            if word in base:
                blocked = True
                break

        if blocked:
            continue

        result.append(item)

    return result