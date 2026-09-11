"""
市場總覽 + 新聞的文字摘要。

Phase 7:market_center 現在可能回報「資料不足」(交易所抓不到行情)。
那種情況**不能**還是輸出一句建議 —— 分不出「市場中性」與「資料壞掉」
正是 Phase 0 稽核列出的問題。資料不夠就明說資料不夠。

這份報告只是資訊呈現,不是交易決策。實際進出場一律走
Signal Pipeline -> Risk Engine -> Execution Engine。
"""
import logging

from market_center import SENTIMENT_UNKNOWN, get_market_center
from news_center import get_crypto_news

logger = logging.getLogger("agmcis.ai_report")

MAX_HEADLINES = 3


def _recommendation(sentiment, best_symbol):
    if sentiment == SENTIMENT_UNKNOWN:
        return "行情資料不足,無法判斷市場方向。請先確認交易所連線再看這份報告。"

    if sentiment == "偏多":
        return f"市場偏多，可優先觀察 {best_symbol} 做多機會。"

    if sentiment == "偏空":
        return "市場偏空，建議降低槓桿與控制風險。"

    if best_symbol:
        return f"市場中性，可關注 {best_symbol} 是否出現突破訊號。"

    return "市場中性。"


def generate_ai_report():
    market = get_market_center()

    try:
        news = get_crypto_news()
    except Exception as exc:
        # 新聞掛掉不該讓整份報告消失,但也不能假裝沒發生
        logger.warning("新聞來源失敗,報告只含行情:%s", exc)
        news = []

    sentiment = market["market_sentiment"]
    best_symbol = market.get("best_symbol")

    return {
        "sentiment": sentiment,
        "score": market.get("market_score"),
        "best_symbol": best_symbol,
        "recommendation": _recommendation(sentiment, best_symbol),
        "headlines": [item["title"] for item in (news or [])[:MAX_HEADLINES]],
        "data_available": sentiment != SENTIMENT_UNKNOWN,
        "available_count": market.get("available_count"),
        "requested_count": market.get("requested_count"),
    }
