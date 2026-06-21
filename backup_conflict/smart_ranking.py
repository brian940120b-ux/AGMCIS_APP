from market_center import get_market_center
from market_data import get_ohlcv
from strategy import analyze_symbol
from news_impact import get_news_impact


def get_smart_ranking():
    market = get_market_center()
    ranking = []

    for item in market["market_data"]:
        symbol = item["symbol"]

        try:
            df = get_ohlcv(symbol)
            signal = analyze_symbol(symbol, df)

            score = 0

            score += signal["score"]

            volume_score = min(
                item["volume_24h"] / 10000000,
                20
            )

            score += volume_score

            score += max(
                item["change_24h"],
                0
            )

            news_bonus = get_news_impact(symbol)

            final_score = score + news_bonus
            final_score = max(0, min(100, final_score))

            ranking.append({
                "symbol": symbol,
                "score": round(final_score, 2),
                "technical_score": round(score, 2),
                "news_impact": news_bonus,
                "signal": signal["signal"],
                "price": signal["price"]
            })

        except Exception:
            pass

    ranking.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return ranking

