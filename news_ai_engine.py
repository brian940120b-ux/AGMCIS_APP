from news_center import get_crypto_news


BULLISH_KEYWORDS = [
    "surge",
    "rally",
    "breakout",
    "approval",
    "approved",
    "etf inflow",
    "bullish",
    "record high",
    "institutional",
    "adoption",
    "buying",
    "accumulate"
]

BEARISH_KEYWORDS = [
    "fall",
    "falls",
    "drop",
    "drops",
    "sell off",
    "bearish",
    "ban",
    "hack",
    "malware",
    "lawsuit",
    "regulator",
    "below",
    "losing",
    "outflow",
    "liquidation"
]

SYMBOL_KEYWORDS = {
    "BTC/USDT": ["bitcoin", "btc"],
    "ETH/USDT": ["ethereum", "eth"],
    "XRP/USDT": ["xrp", "ripple"],
    "SOL/USDT": ["solana", "sol"],
    "DOGE/USDT": ["dogecoin", "doge"]
}


def analyze_news_sentiment():
    news = get_crypto_news()
    analyzed = []

    for item in news:
        title = item["title"]
        lower_title = title.lower()

        bullish_score = 0
        bearish_score = 0

        for word in BULLISH_KEYWORDS:
            if word in lower_title:
                bullish_score += 15

        for word in BEARISH_KEYWORDS:
            if word in lower_title:
                bearish_score += 15

        affected_symbols = []

        for symbol, keywords in SYMBOL_KEYWORDS.items():
            for keyword in keywords:
                if keyword in lower_title:
                    affected_symbols.append(symbol)
                    break

        if bullish_score > bearish_score:
            sentiment = "Bullish"
            score = min(100, 50 + bullish_score - bearish_score)
        elif bearish_score > bullish_score:
            sentiment = "Bearish"
            score = min(100, 50 + bearish_score - bullish_score)
        else:
            sentiment = "Neutral"
            score = 50

        analyzed.append({
            "title": title,
            "source": item["source"],
            "url": item["url"],
            "sentiment": sentiment,
            "score": score,
            "affected_symbols": affected_symbols
        })

    return analyzed