from news_ai_engine import analyze_news_sentiment

news = analyze_news_sentiment()

print()
print("========== AGMCIS V17.1 AI News Sentiment ==========")

for index, item in enumerate(news, start=1):
    print()
    print(f"{index}. {item['title']}")
    print(f"Source: {item['source']}")
    print(f"Sentiment: {item['sentiment']}")
    print(f"Score: {item['score']}")
    print(f"Affected: {item['affected_symbols']}")

print()
print("====================================================")