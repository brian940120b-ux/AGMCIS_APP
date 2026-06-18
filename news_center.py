import feedparser


RSS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed"
]


def get_crypto_news():
    news = []

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)

            for entry in feed.entries[:5]:
                news.append({
                    "title": entry.get("title", "No Title"),
                    "source": feed.feed.get("title", "Crypto News"),
                    "url": entry.get("link", "#")
                })

        except Exception as e:
            news.append({
                "title": f"RSS 讀取失敗：{str(e)}",
                "source": "SYSTEM",
                "url": "#"
            })

    return news[:10]