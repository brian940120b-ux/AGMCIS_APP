import ccxt

exchange = ccxt.binance()


SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "DOGE/USDT"
]


def get_market_center():
    market_data = []

    for symbol in SYMBOLS:
        try:
            ticker = exchange.fetch_ticker(symbol)

            last = ticker.get("last", 0)
            percentage = ticker.get("percentage", 0)
            quote_volume = ticker.get("quoteVolume", 0)

            market_data.append({
                "symbol": symbol,
                "price": round(last, 4),
                "change_24h": round(percentage, 2) if percentage is not None else 0,
                "volume_24h": round(quote_volume, 2) if quote_volume is not None else 0
            })

        except Exception:
            market_data.append({
                "symbol": symbol,
                "price": 0,
                "change_24h": 0,
                "volume_24h": 0
            })

    gainers = sorted(
        market_data,
        key=lambda x: x["change_24h"],
        reverse=True
    )

    volume_rank = sorted(
        market_data,
        key=lambda x: x["volume_24h"],
        reverse=True
    )

    best_symbol = gainers[0]["symbol"] if gainers else "-"

    market_score = 0

    positive_count = len([
        item for item in market_data
        if item["change_24h"] > 0
    ])

    if positive_count >= 4:
        market_sentiment = "偏多"
        market_score = 80
    elif positive_count >= 2:
        market_sentiment = "中性"
        market_score = 50
    else:
        market_sentiment = "偏空"
        market_score = 25

    return {
        "market_data": market_data,
        "gainers": gainers,
        "volume_rank": volume_rank,
        "best_symbol": best_symbol,
        "market_sentiment": market_sentiment,
        "market_score": market_score
    }