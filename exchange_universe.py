"""
可交易標的清單。

Phase 2 的修正:**移除 OKX。**

原本這裡同時查 OKX 與 BingX 的 fetch_tickers(),把兩邊的成交量加總來排名,
然後把排名結果丟給 opportunity_scanner 開倉 ——
所以系統可能對「BingX 根本沒有掛牌」的標的下單,而且成交量數字混了另一間交易所。

現在只查 BingX 的 USDT 永續合約,系統只會看到自己真正能交易的東西。
"""
import logging

from agmcis.core.enums import MarketType
from agmcis.core.errors import ExchangeUnavailableError

logger = logging.getLogger("agmcis.exchange_universe")

# 槓桿代幣與股票型商品,合約系統不交易
BLOCK_WORDS = [
    "UP", "DOWN", "BULL", "BEAR",
    "3L", "3S", "5L", "5S",
    "AAPL", "TSLA", "MSFT", "AMZN", "GOOG", "META", "NVDA",
]


def is_valid_symbol(symbol: str) -> bool:
    """symbol 可能是 'BTC/USDT' 或合約格式 'BTC/USDT:USDT'。"""
    if not symbol:
        return False

    base_pair = symbol.split(":")[0]

    if not base_pair.endswith("/USDT"):
        return False

    base = base_pair.split("/")[0]

    if len(base) < 2 or base.startswith("$"):
        return False

    return not any(word in base for word in BLOCK_WORDS)


def get_top_volume_symbols(limit=50, market_type=MarketType.PERPETUAL):
    """
    依 24 小時成交額排序的 BingX 合約清單。

    回傳格式維持與舊版相同(symbol / volume / exchanges),呼叫端不用改。
    交易所不可用時回傳空清單並記錄 —— 空清單會讓排名是空的,
    也就是不會有任何開倉候選,這正是資料不可信時應有的行為。
    """
    from agmcis.data.market_data import get_adapter

    adapter = get_adapter()
    market_type = MarketType.parse(market_type, MarketType.PERPETUAL)

    try:
        tickers = adapter.get_tickers(market_type)
    except ExchangeUnavailableError as exc:
        logger.error("無法取得 BingX ticker 清單,回傳空的可交易標的: %s", exc)
        return []

    candidates = []

    for symbol, ticker in tickers.items():
        if not is_valid_symbol(symbol):
            continue

        volume = ticker.get("quoteVolume") or 0
        if not volume:
            continue

        candidates.append({
            # 統一去掉合約後綴,下游都用 'BTC/USDT' 這種格式
            "symbol": symbol.split(":")[0],
            "volume": float(volume),
            "exchanges": ["bingx"],
            "market_type": market_type.value,
        })

    candidates.sort(key=lambda item: item["volume"], reverse=True)
    logger.info("BingX %s 可交易標的 %d 檔", market_type.value, len(candidates))

    return candidates[:limit]
