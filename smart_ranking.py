from market_center import get_market_center
from market_data import get_ohlcv
from strategy import analyze_symbol


def get_smart_ranking():

    market = get_market_center()

    ranking = []

    for item in market["market_data"]:

        symbol = item["symbol"]

        try:
            df = get_ohlcv(symbol)

            signal = analyze_symbol(symbol, df)

            score = 0

            # 技術面
            score += signal["score"]

            # 成交量加分
            volume_score = min(
                item["volume_24h"] / 10000000,
                20
            )

            score += volume_score

            # 漲幅加分
            score += max(
                item["change_24h"],
                0
            )

            ranking.append({
                "symbol": symbol,
                "score": round(score, 2),
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