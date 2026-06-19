# test_news_impact.py

from news_impact import get_news_impact

symbols = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "DOGE/USDT"
]

print()
print("========== AGMCIS News Impact ==========")

for symbol in symbols:
    impact = get_news_impact(symbol)

    print(symbol, impact)

print()
print("========================================")