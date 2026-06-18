from market_center import get_market_center
from news_center import get_crypto_news


def generate_ai_report():

    market = get_market_center()
    news = get_crypto_news()

    sentiment = market["market_sentiment"]
    score = market["market_score"]
    best_symbol = market["best_symbol"]

    if sentiment == "偏多":
        recommendation = f"市場偏多，可優先觀察 {best_symbol} 做多機會。"

    elif sentiment == "偏空":
        recommendation = "市場偏空，建議降低槓桿與控制風險。"

    else:
        recommendation = f"市場中性，可關注 {best_symbol} 是否出現突破訊號。"

    headlines = []

    for item in news[:3]:
        headlines.append(item["title"])

    return {
        "sentiment": sentiment,
        "score": score,
        "best_symbol": best_symbol,
        "recommendation": recommendation,
        "headlines": headlines
    }