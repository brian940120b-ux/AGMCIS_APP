from news_ai_engine import analyze_news_sentiment


def get_news_impact(symbol):
    news = analyze_news_sentiment()

    impact = 0

    for item in news:
        if symbol not in item["affected_symbols"]:
            continue

        if item["sentiment"] == "Bullish":
            impact += item["score"] * 0.15

        elif item["sentiment"] == "Bearish":
            impact -= item["score"] * 0.15

    return round(impact, 2)

