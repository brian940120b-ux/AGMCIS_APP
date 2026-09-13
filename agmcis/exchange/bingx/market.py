"""
BingX 行情(Master Prompt 第八節的 market.py、第十一節)。

## 這一層的核心規則:取不到就是 None,絕不用別的東西冒充

第十一節列的東西不是全部都拿得到 —— ccxt 對 BingX 沒有標準化
long/short 比與爆倉,而標記價不保證出現在 ticker 裡。

每一個「拿不到」都有一個很誘人的替代品:
    標記價拿不到 -> 用 last?
    多空比拿不到 -> 當成 1.0(平衡)?
    訂單簿拿不到 -> 當成沒有失衡?

**三個都不行。** 它們會把「不知道」偽裝成「知道,而且是中性的」,
而那個誤會只有在最極端的行情裡才會被發現 —— 也就是最貴的時候。

強平用的是標記價不是最新成交價;用 last 算強平距離會在插針行情裡
算錯,而那正是最需要算對的時候。
"""
import logging

from agmcis.core.enums import MarketType
from agmcis.exchange.bingx.client import ccxt_type

logger = logging.getLogger("agmcis.exchange.bingx.market")


def as_float(value):
    """轉不動就回 None。回 0 會被當成「標記價是 0」,那比沒有更糟。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick(raw, *keys):
    """
    從 ticker 的頂層或 info 裡找第一個有值的欄位。

    ccxt 對不同交易所把同一個東西放在不同層,而 BingX 常常只在
    info 裡。兩層都找,找不到回 None。
    """
    info = raw.get("info") or {}
    for key in keys:
        if raw.get(key) is not None:
            return as_float(raw.get(key))
        if info.get(key) is not None:
            return as_float(info.get(key))
    return None


class MarketMixin:
    """BingXAdapter 的一部分。需要 client 層的 _call / _optional。"""

    # ---------------- 市場清單 ----------------

    def list_markets(self, market_type=MarketType.PERPETUAL):
        market_type = MarketType.parse(market_type, MarketType.PERPETUAL)
        markets = self.load_markets(market_type)

        wanted = ccxt_type(market_type)
        result = []

        for symbol, market in markets.items():
            if not market.get("active", True):
                continue
            if market.get("type") and market["type"] != wanted:
                continue
            if market.get("quote") != "USDT":
                continue

            result.append({
                "symbol": symbol,
                "base": market.get("base"),
                "quote": market.get("quote"),
                "market_type": market_type.value,
                "contract_size": market.get("contractSize"),
            })

        return result

    # ---------------- 行情 ----------------

    def get_ticker(self, symbol, market_type=MarketType.PERPETUAL):
        market_symbol = self.to_market_symbol(symbol, market_type)
        raw = self._call("fetch_ticker", market_symbol, market_type=market_type)

        return {
            "symbol": symbol,
            "market_symbol": market_symbol,
            "price": raw.get("last"),
            "bid": raw.get("bid"),
            "ask": raw.get("ask"),
            "high_24h": raw.get("high"),
            "low_24h": raw.get("low"),
            "change_pct_24h": raw.get("percentage"),
            "volume_24h": raw.get("baseVolume"),
            "quote_volume_24h": raw.get("quoteVolume"),
            # 標記價與指數價(第十一節)。取不到就是 None,**不退回 last** ——
            # 見模組說明。
            "mark_price": self._mark_price(raw),
            "index_price": self._index_price(raw),
            "timestamp": raw.get("timestamp"),
        }

    @staticmethod
    def _mark_price(raw):
        return _pick(raw, "markPrice", "mark_price")

    @staticmethod
    def _index_price(raw):
        return _pick(raw, "indexPrice", "index_price")

    def get_ohlcv(self, symbol, timeframe="1h", limit=150,
                  market_type=MarketType.PERPETUAL):
        market_symbol = self.to_market_symbol(symbol, market_type)
        return self._call(
            "fetch_ohlcv", market_symbol, timeframe, None, limit,
            market_type=market_type,
        )

    def get_tickers(self, market_type=MarketType.PERPETUAL):
        """一次取得該市場所有合約的 ticker。失敗拋例外。"""
        return self._call("fetch_tickers", market_type=market_type) or {}

    def get_order_book(self, symbol, limit=20, market_type=MarketType.PERPETUAL):
        market_symbol = self.to_market_symbol(symbol, market_type)
        raw = self._optional(
            "fetch_order_book", market_symbol, limit, market_type=market_type
        )
        if not raw:
            return None

        bids = raw.get("bids") or []
        asks = raw.get("asks") or []

        if not bids or not asks:
            return None

        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        bid_volume = sum(float(level[1]) for level in bids)
        ask_volume = sum(float(level[1]) for level in asks)
        total = bid_volume + ask_volume

        mid = (best_bid + best_ask) / 2

        return {
            "symbol": symbol,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": best_ask - best_bid,
            "spread_pct": ((best_ask - best_bid) / mid * 100) if mid > 0 else None,
            "bid_volume": bid_volume,
            "ask_volume": ask_volume,
            # > 0 代表買盤較厚,< 0 代表賣盤較厚。範圍 -1 ~ 1。
            "imbalance": ((bid_volume - ask_volume) / total) if total > 0 else None,
            "timestamp": raw.get("timestamp"),
        }

    # ---------------- 情緒與衍生品資料(第十一節) ----------------

    def get_long_short_ratio(self, symbol, market_type=MarketType.PERPETUAL):
        """
        多空持倉人數比。**取不到就回 None** —— 這個數字最常見的誤用是
        「沒有資料時當成 1.0(多空平衡)」,那會讓一個完全沒有情緒資訊的
        時刻看起來像一個確定中性的時刻。

        > 1 代表做多的人比較多。極端值通常是反指標:大家都站同一邊的
        時候,那一邊的停損就是燃料。
        """
        raw = self._optional(
            "fetch_long_short_ratio_history",
            self.to_market_symbol(symbol, market_type),
            market_type=market_type,
        )

        if not raw:
            return None

        latest = raw[-1] if isinstance(raw, list) else raw
        if not isinstance(latest, dict):
            return None

        ratio = as_float(latest.get("longShortRatio")) or as_float(
            (latest.get("info") or {}).get("longShortRatio")
        )

        if ratio is None:
            return None

        return {
            "symbol": symbol,
            "ratio": ratio,
            "timestamp": latest.get("timestamp"),
        }

    def get_liquidations(self, symbol, limit=50, market_type=MarketType.PERPETUAL):
        """
        近期爆倉。取不到回 None。

        爆倉資料的價值不在總量,在**方向**:一串多單爆倉代表下方的
        槓桿被清掉了,那通常是反彈的前提。但這個資料在多數交易所是
        延遲的,所以它是背景資訊,不是進場訊號。
        """
        raw = self._optional(
            "fetch_liquidations",
            self.to_market_symbol(symbol, market_type),
            None, limit,
            market_type=market_type,
        )

        if not raw:
            return None

        events = [
            {
                "price": as_float(item.get("price")),
                "amount": as_float(item.get("baseValue") or item.get("amount")),
                "side": item.get("side"),
                "timestamp": item.get("timestamp"),
            }
            for item in raw
            if isinstance(item, dict)
        ]

        longs = sum(1 for e in events if e["side"] == "sell")
        shorts = sum(1 for e in events if e["side"] == "buy")

        return {
            "symbol": symbol,
            "count": len(events),
            # 多單爆倉是被強制賣出,所以 side 是 sell。
            "long_liquidations": longs,
            "short_liquidations": shorts,
            "events": events[-10:],
        }

    def get_funding_rate(self, symbol, market_type=MarketType.PERPETUAL):
        market_type = MarketType.parse(market_type, MarketType.PERPETUAL)

        # 只有永續有資金費率。Standard 本來就不支援,
        # 但即使未來支援了,它也沒有資金費率 —— 不該白打一次 API。
        if market_type is not MarketType.PERPETUAL:
            return None

        market_symbol = self.to_market_symbol(symbol, market_type)
        raw = self._optional("fetch_funding_rate", market_symbol, market_type=market_type)
        if not raw:
            return None

        return {
            "symbol": symbol,
            "funding_rate": raw.get("fundingRate"),
            # 這個欄位叫 next,所以先讀 next。
            #
            # 第一版先讀 fundingTimestamp —— 那在 ccxt 的語意裡是**這一次**
            # (上一次)結算的時間,不是下一次。bingx 目前一律把它設成
            # None,所以退路救了它;但只要哪天 ccxt 開始填那個欄位,
            # 這裡就會在一個叫 next 的欄位裡放上一次的時間。
            "next_funding_time": (
                raw.get("nextFundingTimestamp") or raw.get("fundingTimestamp")
            ),
            "mark_price": raw.get("markPrice"),
            "index_price": raw.get("indexPrice"),
        }

    def get_open_interest(self, symbol, market_type=MarketType.PERPETUAL):
        market_symbol = self.to_market_symbol(symbol, market_type)
        raw = self._optional("fetch_open_interest", market_symbol, market_type=market_type)
        if not raw:
            return None

        # ⚠️ 這兩個欄位**單位不同**,而且不會同時有值。
        #
        # ccxt 4.5.78 的 bingx parse_open_interest():
        #     openInterestAmount = openInterest if isInverse else None
        #     openInterestValue  = None if isInverse else openInterest
        #
        # 我們交易的是 USDT 本位(linear)永續,所以 **amount 永遠是 None**,
        # 數字在 value 裡,單位是 USDT。
        #
        # 第一版寫 `amount or value`,把兩個不同單位的量塞進同一個欄位:
        # 反向合約給的是張數,正向合約給的是 USDT,而欄位名叫
        # open_interest 讓人以為是張數。目前沒有 Agent 拿它去算東西,
        # 所以還沒出事 —— 這是給下一個寫 Agent 的人挖的坑,先填掉。
        amount = raw.get("openInterestAmount")
        value = raw.get("openInterestValue")

        return {
            "symbol": symbol,
            # 張數。linear 永續拿不到,所以正常情況下就是 None。
            "open_interest_amount": amount,
            # 名目價值(USDT)。linear 永續的數字在這裡。
            "open_interest_value": value,
            # 相容舊呼叫端。單位跟著來源走 —— 要拿去算的人請改用
            # 上面兩個之一,不要用這個。
            "open_interest": value if value is not None else amount,
            "timestamp": raw.get("timestamp"),
        }
