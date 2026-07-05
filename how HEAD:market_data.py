import ccxt

EXCHANGES = {
    "okx": ccxt.okx(),
    "bingx": ccxt.bingx()
}

def get_exchange(name):
    return EXCHANGES.get(name)

def test_connection():
    result = {}
    for name, exchange in EXCHANGES.items():
        try:
            ticker = exchange.fetch_ticker("BTC/USDT")
            result[name] = {
                "success": True,
                "last": ticker["last"]
            }
        except Exception as e:
            result[name] = {
                "success": False,
                "error": str(e)
            }
    return result

def fetch_ticker_safe(symbol):
    for name in ["okx", "bingx"]:
        try:
            ticker = EXCHANGES[name].fetch_ticker(symbol)
            return {
                "exchange": name,
                "ticker": ticker
            }
        except Exception:
            continue

    return None

def get_price_safe(symbol):
    result = fetch_ticker_safe(symbol)
    if not result:
        return None

    ticker = result.get("ticker", {})
    return {
        "symbol": symbol,
        "price": ticker.get("last"),
        "exchange": result.get("exchange")
    }

def fetch_ohlcv_safe(symbol, timeframe="1h", limit=200):
    for name in ["okx", "bingx"]:
        try:
            data = EXCHANGES[name].fetch_ohlcv(
                symbol,
                timeframe=timeframe,
                limit=limit
            )
            return {
                "exchange": name,
                "ohlcv": data
            }
        except Exception:
            continue

    return None
