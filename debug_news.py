"""
新聞 API 的回應格式檢查小工具。

原本整段寫在模組層 —— import 這個檔案就會打外部 API 並把整包 JSON 印出來。
Phase 1 已經把同類問題從 backtest.py / strategy_lab.py 移掉,這裡補上最後一個。
"""
import requests

NEWS_URL = "https://min-api.cryptocompare.com/data/v2/news/?lang=EN"
TIMEOUT_SECONDS = 10


def fetch_news(url=NEWS_URL):
    response = requests.get(url, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def main():
    data = fetch_news()
    print(type(data))
    print(data.keys())
    print()
    print(data)


if __name__ == "__main__":
    main()
