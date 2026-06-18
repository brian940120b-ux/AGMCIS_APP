from news_center import get_crypto_news

news = get_crypto_news()

print()
print("========== AGMCIS News Center ==========")

for index, item in enumerate(news, start=1):
    print()
    print(f"{index}. {item['title']}")
    print(item["source"])

print()
print("========================================")