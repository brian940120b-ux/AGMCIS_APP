from news_ai_engine import analyze_news_sentiment
from notifier import send_telegram


MIN_NEWS_SCORE = 65


def push_important_news():
    news = analyze_news_sentiment()

    important_news = []

    for item in news:
        if item["score"] >= MIN_NEWS_SCORE and item["affected_symbols"]:
            important_news.append(item)

    if not important_news:
        print("目前沒有重要新聞需要推送")
        return

    for item in important_news[:5]:
        symbols = ", ".join(item["affected_symbols"])

        message = f"""
📰 AGMCIS AI NEWS

標題：
{item["title"]}

情緒：
{item["sentiment"]}

分數：
{item["score"]}/100

影響幣種：
{symbols}

來源：
{item["source"]}

連結：
{item["url"]}

提醒：
此為新聞情緒分析，不代表一定進場。
"""

        send_telegram(message)

    print(f"已推送 {len(important_news[:5])} 則重要新聞")