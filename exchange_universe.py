import ccxt


EXCHANGES = {
    "binance": ccxt.binance(),
    "okx": ccxt.okx(),
    "bingx": ccxt.bingx()
}


BLOCK_WORDS = [
    "UP",
    "DOWN",
    "BULL",
    "BEAR",
    "3L",
    "3S",
    "5L",
    "5S"
]


def is_valid_symbol(symbol):
    if not symbol.endswith("/USDT"):
        return False

    if ":" in symbol:
        return False

    base = symbol.split("/")[0]

    if len(base) < 2:
        return False

    if base.startswith("$"):
        return False

    for word in BLOCK_WORDS:
        if word in base:
            return False

    return True


def get_top_volume_symbols(limit=50):

    candidates = {}

    for exchange_name, exchange in EXCHANGES.items():

        print(f"Loading {exchange_name}...")

        try:

            exchange.load_markets()

            tickers = exchange.fetch_tickers()

            for symbol, ticker in tickers.items():

                if not is_valid_symbol(symbol):
                    continue

                volume = ticker.get("quoteVolume")

                if volume is None:
                    volume = 0

                if symbol not in candidates:

                    candidates[symbol] = {
                        "symbol": symbol,
                        "volume": 0,
                        "exchanges": []
                    }

                candidates[symbol]["volume"] += volume
                candidates[symbol]["exchanges"].append(exchange_name)

        except Exception as e:

            print(exchange_name, e)

    result = list(candidates.values())

    result.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    return result[:limit]