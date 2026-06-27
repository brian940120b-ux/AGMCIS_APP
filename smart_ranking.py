from market_data import get_ohlcv
from logger_service import logger
from strategy import analyze_symbol
from news_impact import get_news_impact
from exchange_universe import get_top_volume_symbols
from symbol_filter import filter_symbols
from optimizer_bonus import get_optimizer_bonus


def get_smart_ranking():
    market = filter_symbols(
        get_top_volume_symbols(limit=50)
    )

    ranking = []

    for item in market:
        symbol = item["symbol"]

        try:
            df = get_ohlcv(symbol)
            signal = analyze_symbol(symbol, df)

            score = 0

            technical_score = signal["score"]
            score += technical_score

            volume_score = min(
                item["volume"] / 10000000,
                20
            )

            score += volume_score

            news_bonus = get_news_impact(symbol)
            optimizer_bonus = get_optimizer_bonus(symbol)

            final_score = score + news_bonus + optimizer_bonus
            final_score = max(0, min(100, final_score))

            ranking.append({
                "symbol": symbol,
                "score": round(final_score, 2),
                "technical_score": round(technical_score, 2),
                "volume_score": round(volume_score, 2),
                "news_impact": news_bonus,
                "optimizer_bonus": optimizer_bonus,
                "signal": signal["signal"],
                "price": signal["price"],
                "exchanges": item["exchanges"],
                "volume": round(item["volume"], 2)
            })

        except Exception as e:
            logger.exception(f"{symbol} ranking error: {e}")

    ranking.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return ranking