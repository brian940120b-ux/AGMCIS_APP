"""
十二個內建 Agent。

每個 Agent 只負責一件事,而且在自己那件事沒發生時**棄權**而不是反對。
這是 Phase 6 學到的:把棄權當反對票,系統會永遠不交易。

分成三組:

  方向型(1-6)   從不同角度判斷該不該進場、往哪邊
  環境型(7-9)   判斷這個環境適不適合交易(多半只投 WAIT 或棄權)
  管理型(10-12) 處理已有部位、帳戶層風險與外部資訊

沒有任何一個 Agent 拿得到交易所連線或下單函式。
"""
from agmcis.agents.base import BaseAgent, Vote

# ---------------------------------------------------------------- 方向型


class TrendAgent(BaseAgent):
    """EMA 排列 + ADX 強度。順勢的骨幹。"""
    name = "trend"
    weight = 1.5

    MIN_ADX = 20

    def _analyse(self, context):
        indicators = context.indicators
        ema20, ema50 = indicators.ema20, indicators.ema50
        adx = indicators.adx

        if ema20 is None or ema50 is None:
            return self.abstain("沒有 EMA")

        if adx is not None and adx < self.MIN_ADX:
            # 盤整時趨勢 Agent 沒有話語權,但它看得懂這個盤 —— 投 WAIT。
            return self.wait(f"ADX {adx:.1f} < {self.MIN_ADX},沒有趨勢")

        gap_pct = abs(ema20 - ema50) / ema50 * 100 if ema50 else 0
        confidence = min(100.0, 50 + gap_pct * 10 + (adx or 20))

        if ema20 > ema50:
            return self.opinion(Vote.LONG, confidence,
                                [f"EMA20 > EMA50(差 {gap_pct:.2f}%),ADX {adx}"])
        return self.opinion(Vote.SHORT, confidence,
                            [f"EMA20 < EMA50(差 {gap_pct:.2f}%),ADX {adx}"])


class MomentumAgent(BaseAgent):
    """MACD 柱狀圖的方向與斜率。"""
    name = "momentum"
    weight = 1.2

    def _analyse(self, context):
        hist = context.indicators.macd_hist

        if hist is None:
            return self.abstain("沒有 MACD")

        price = context.indicators.price or context.price
        if not price:
            return self.abstain("沒有價格,無法換算動能強度")

        strength = abs(hist) / price * 100
        confidence = min(100.0, 40 + strength * 200)

        if hist > 0:
            return self.opinion(Vote.LONG, confidence, [f"MACD 柱狀圖 {hist:.6f} > 0"])
        if hist < 0:
            return self.opinion(Vote.SHORT, confidence, [f"MACD 柱狀圖 {hist:.6f} < 0"])

        return self.wait("MACD 柱狀圖為 0")


class MeanReversionAgent(BaseAgent):
    """
    RSI 極端 + 布林通道。**只在盤整市況發言**。

    在強趨勢裡逆勢接刀是虧損的主要來源之一,所以趨勢明確時直接棄權,
    不是投反對票。
    """
    name = "mean_reversion"
    weight = 1.0

    OVERSOLD = 30
    OVERBOUGHT = 70

    def _analyse(self, context):
        regime = context.regime
        if regime is not None and getattr(regime.regime, "is_trending", False):
            return self.abstain("趨勢市,均值回歸不適用")

        rsi = context.indicators.rsi
        if rsi is None:
            return self.abstain("沒有 RSI")

        price = context.indicators.price or context.price
        lower, upper = context.indicators.bb_lower, context.indicators.bb_upper

        if rsi <= self.OVERSOLD:
            confidence = min(100.0, 50 + (self.OVERSOLD - rsi) * 2)
            reasons = [f"RSI {rsi:.1f} 超賣"]
            if price and lower and price <= lower:
                confidence = min(100.0, confidence + 15)
                reasons.append("價格跌破布林下軌")
            return self.opinion(Vote.LONG, confidence, reasons)

        if rsi >= self.OVERBOUGHT:
            confidence = min(100.0, 50 + (rsi - self.OVERBOUGHT) * 2)
            reasons = [f"RSI {rsi:.1f} 超買"]
            if price and upper and price >= upper:
                confidence = min(100.0, confidence + 15)
                reasons.append("價格突破布林上軌")
            return self.opinion(Vote.SHORT, confidence, reasons)

        return self.abstain(f"RSI {rsi:.1f} 不在極端區")


class BreakoutAgent(BaseAgent):
    """布林通道寬度收縮後的突破。壓縮沒發生就棄權。"""
    name = "breakout"
    weight = 1.0

    SQUEEZE_WIDTH_PCT = 4.0

    def _analyse(self, context):
        indicators = context.indicators
        width = indicators.bb_width_pct
        price = indicators.price or context.price

        if width is None or price is None:
            return self.abstain("沒有布林通道資料")

        if width > self.SQUEEZE_WIDTH_PCT:
            return self.abstain(f"通道寬度 {width:.2f}% 沒有壓縮")

        upper, lower = indicators.bb_upper, indicators.bb_lower
        if upper is None or lower is None:
            return self.abstain("沒有布林上下軌")

        confidence = min(100.0, 50 + (self.SQUEEZE_WIDTH_PCT - width) * 10)

        if price >= upper:
            return self.opinion(Vote.LONG, confidence,
                                [f"壓縮({width:.2f}%)後向上突破"])
        if price <= lower:
            return self.opinion(Vote.SHORT, confidence,
                                [f"壓縮({width:.2f}%)後向下突破"])

        return self.wait(f"通道壓縮({width:.2f}%)但還沒突破")


class VolumeAgent(BaseAgent):
    """
    成交量確認。**不主張方向,只確認或否定**。

    量能不足時投 WAIT(它確實看得懂),量能資料缺失時棄權。
    """
    name = "volume"
    weight = 0.8

    def _analyse(self, context):
        volume = context.indicators.volume
        volume_ma = context.indicators.volume_ma20

        if volume is None or not volume_ma:
            return self.abstain("沒有成交量資料")

        ratio = volume / volume_ma

        if ratio < 0.8:
            # 這裡一定要給信心值。信心 0 的 WAIT 在共識裡權重是 0,
            # 等於投了一票沒有力道的反對 —— 那不是這個 Agent 的本意。
            return self.wait(f"量能 {ratio:.2f}x 均量,明顯不足", 60)

        if ratio < 1.0:
            return self.abstain(f"量能 {ratio:.2f}x 均量,沒有確認也沒有否定")

        # 有量就跟隨當下趨勢,沒有自己的方向主張
        trend = context.indicators.trend
        confidence = min(100.0, 40 + (ratio - 1) * 60)

        if trend == "BULLISH":
            return self.opinion(Vote.LONG, confidence, [f"量能 {ratio:.2f}x 確認多方"])
        if trend == "BEARISH":
            return self.opinion(Vote.SHORT, confidence, [f"量能 {ratio:.2f}x 確認空方"])

        return self.abstain("量能足夠但趨勢不明")


class BtcCorrelationAgent(BaseAgent):
    """
    山寨幣看 BTC 臉色。BTC 資料沒帶進來就棄權;標的本身是 BTC 也棄權。
    """
    name = "btc_correlation"
    weight = 0.8

    def _analyse(self, context):
        if context.symbol.upper().startswith("BTC"):
            return self.abstain("標的本身是 BTC")

        btc = context.btc_indicators
        if btc is None or not getattr(btc, "data_ok", False):
            return self.abstain("沒有 BTC 參考資料")

        trend = btc.trend
        if trend == "UNKNOWN":
            return self.abstain("BTC 趨勢不明")

        if trend == "BULLISH":
            return self.opinion(Vote.LONG, 55, ["BTC 偏多,山寨幣順風"])
        return self.opinion(Vote.SHORT, 55, ["BTC 偏空,山寨幣逆風"])


# ---------------------------------------------------------------- 環境型


class VolatilityAgent(BaseAgent):
    """
    波動度守門。極端波動時投 WAIT —— 那種環境下停損很容易被無意義地掃掉。

    這個 Agent 從不主張方向。
    """
    name = "volatility"
    weight = 1.3

    EXTREME_ATR_PCT = 5.0
    HIGH_ATR_PCT = 3.0

    def _analyse(self, context):
        indicators = context.indicators
        atr, price = indicators.atr, (indicators.price or context.price)

        if atr is None or not price:
            return self.abstain("沒有 ATR")

        atr_pct = atr / price * 100

        if atr_pct >= self.EXTREME_ATR_PCT:
            return self.wait(f"ATR {atr_pct:.2f}% 屬極端波動,停損極易被掃", 90)

        if atr_pct >= self.HIGH_ATR_PCT:
            return self.wait(f"ATR {atr_pct:.2f}% 偏高", 55)

        return self.abstain(f"ATR {atr_pct:.2f}% 正常,無意見")


class RegimeAgent(BaseAgent):
    """市況守門。市況不可交易時投 WAIT。"""
    name = "regime"
    weight = 1.4

    def _analyse(self, context):
        regime = context.regime
        if regime is None:
            return self.abstain("沒有市況判斷")

        if not regime.is_tradeable:
            return self.wait(
                f"市況 {regime.regime.value} / 波動 {regime.volatility.value} 不可交易",
                85,
            )

        if regime.regime.is_bullish:
            return self.opinion(Vote.LONG, 60, [f"市況 {regime.regime.value}"])
        if regime.regime.is_bearish:
            return self.opinion(Vote.SHORT, 60, [f"市況 {regime.regime.value}"])

        return self.abstain(f"市況 {regime.regime.value},無方向偏好")


class FundingAgent(BaseAgent):
    """
    永續資金費率。極端費率代表單邊過度擁擠,是反向訊號。

    沒有資金費率就棄權 —— 不要因為抓不到資料就假裝費率是 0。
    """
    name = "funding"
    weight = 0.7

    EXTREME_RATE = 0.001        # 0.1% / 8h

    def _analyse(self, context):
        rate = context.funding_rate
        if rate is None:
            return self.abstain("沒有資金費率")

        if abs(rate) < self.EXTREME_RATE:
            return self.abstain(f"資金費率 {rate:.5f} 正常")

        confidence = min(100.0, 40 + abs(rate) / self.EXTREME_RATE * 20)

        if rate > 0:
            return self.opinion(Vote.SHORT, confidence,
                                [f"資金費率 {rate:.5f} 偏高,多方過度擁擠"])
        return self.opinion(Vote.LONG, confidence,
                            [f"資金費率 {rate:.5f} 偏負,空方過度擁擠"])


# ---------------------------------------------------------------- 管理型


class PositionRiskAgent(BaseAgent):
    """
    帳戶層風險。已有同向相關部位時投 WAIT ——
    三個高相關標的的同向部位,實際上是一個三倍大的部位。

    這不取代 Risk Engine,它只是讓共識階段就先看到這件事。
    """
    name = "position_risk"
    weight = 1.2

    MAX_CORRELATED = 2

    def _analyse(self, context):
        if len(context.correlated_symbols) >= self.MAX_CORRELATED:
            return self.wait(
                f"已有 {len(context.correlated_symbols)} 個相關部位"
                f"({', '.join(context.correlated_symbols)}),集中度過高",
                80,
            )

        return self.abstain("帳戶層風險無異常")


class ExitAgent(BaseAgent):
    """
    已有部位的管理。**只在有部位時發言。**

    這個 Agent 的意見不會變成新的進場 TradeIntent ——
    它的輸出是給部位管理用的,Consensus 會把它排除在進場投票之外。
    """
    name = "exit"
    weight = 1.0
    requires_position = True

    STOP_DISTANCE_WARNING_PCT = 1.0

    def _analyse(self, context):
        position = context.position
        price = context.indicators.price or context.price

        if not price:
            return self.abstain("沒有現價")

        stop_loss = position.get("stop_loss") or position.get("stoploss")
        if stop_loss:
            distance_pct = abs(price - float(stop_loss)) / price * 100
            if distance_pct <= self.STOP_DISTANCE_WARNING_PCT:
                return self.wait(
                    f"距離停損只剩 {distance_pct:.2f}%,隨時可能出場", 85,
                )

        rsi = context.indicators.rsi
        direction = str(position.get("direction") or position.get("signal") or "")

        if rsi is not None:
            if "多" in direction and rsi >= 75:
                return self.wait(f"持有多單但 RSI {rsi:.1f} 超買,考慮減碼", 70)
            if "空" in direction and rsi <= 25:
                return self.wait(f"持有空單但 RSI {rsi:.1f} 超賣,考慮減碼", 70)

        return self.abstain("部位狀態正常")


class NewsAgent(BaseAgent):
    """
    外部消息面。沒有資料就棄權 —— 這是最常沒有資料的 Agent,
    而「沒有新聞」不等於「新聞中性」。
    """
    name = "news"
    weight = 0.5

    STRONG_IMPACT = 5.0

    def _analyse(self, context):
        impact = context.news_impact
        if impact is None:
            return self.abstain("沒有新聞資料")

        if abs(impact) < self.STRONG_IMPACT:
            return self.abstain(f"新聞影響 {impact:+.1f} 不顯著")

        confidence = min(100.0, 40 + abs(impact) * 3)

        if impact > 0:
            return self.opinion(Vote.LONG, confidence, [f"新聞面偏多 {impact:+.1f}"])
        return self.opinion(Vote.SHORT, confidence, [f"新聞面偏空 {impact:+.1f}"])


DIRECTIONAL_AGENTS = [
    TrendAgent, MomentumAgent, MeanReversionAgent,
    BreakoutAgent, VolumeAgent, BtcCorrelationAgent,
]

ENVIRONMENT_AGENTS = [VolatilityAgent, RegimeAgent, FundingAgent]

MANAGEMENT_AGENTS = [PositionRiskAgent, ExitAgent, NewsAgent]

ALL_AGENT_CLASSES = DIRECTIONAL_AGENTS + ENVIRONMENT_AGENTS + MANAGEMENT_AGENTS
