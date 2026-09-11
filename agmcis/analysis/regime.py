"""
Market Regime Engine(Master Prompt 第 23 條)。

為什麼重要:同一套策略在不同市況下表現天差地別。
趨勢跟隨策略在盤整市會被反覆洗出場;均值回歸策略在單邊趨勢市會一路加碼到爆。
不分市況就套用同一組策略,等於把一半的時間交給不適合的工具。

這一層只**描述**市場現在長什麼樣子,不下交易判斷。
判斷是策略層的事,而策略會依據 regime 決定自己要不要出手。
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class Regime(str, Enum):
    STRONG_BULL = "STRONG_BULL"
    BULL = "BULL"
    RANGE = "RANGE"
    BEAR = "BEAR"
    STRONG_BEAR = "STRONG_BEAR"
    UNKNOWN = "UNKNOWN"

    @property
    def is_trending(self):
        return self in (
            Regime.STRONG_BULL, Regime.BULL, Regime.BEAR, Regime.STRONG_BEAR
        )

    @property
    def is_bullish(self):
        return self in (Regime.STRONG_BULL, Regime.BULL)

    @property
    def is_bearish(self):
        return self in (Regime.STRONG_BEAR, Regime.BEAR)


class Volatility(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    EXTREME = "EXTREME"
    UNKNOWN = "UNKNOWN"


# ATR 佔價格比例的分界
VOLATILITY_BANDS = [
    (5.0, Volatility.EXTREME),
    (3.0, Volatility.HIGH),
    (1.0, Volatility.NORMAL),
]


@dataclass
class MarketRegime:
    regime: Regime = Regime.UNKNOWN
    volatility: Volatility = Volatility.UNKNOWN
    adx: Optional[float] = None
    atr_pct: Optional[float] = None
    reasons: List[str] = field(default_factory=list)

    @property
    def is_tradeable(self):
        """
        UNKNOWN 不可交易。極端波動也不可交易 ——
        那種環境下停損很容易被無意義地掃到。
        """
        return (
            self.regime is not Regime.UNKNOWN
            and self.volatility is not Volatility.EXTREME
        )

    def favours(self, direction_value):
        """這個市況偏向做多還是做空。用來在評分時加減分。"""
        if self.regime.is_bullish:
            return direction_value == "做多"
        if self.regime.is_bearish:
            return direction_value == "做空"
        return False

    def to_dict(self):
        return {
            "regime": self.regime.value,
            "volatility": self.volatility.value,
            "adx": self.adx,
            "atr_pct": self.atr_pct,
            "is_trending": self.regime.is_trending,
            "is_tradeable": self.is_tradeable,
            "reasons": list(self.reasons),
        }


def classify_volatility(atr_pct):
    if atr_pct is None:
        return Volatility.UNKNOWN
    for threshold, band in VOLATILITY_BANDS:
        if atr_pct >= threshold:
            return band
    return Volatility.LOW


def detect(indicators, btc_indicators=None):
    """
    判斷市場狀態。

    btc_indicators 是大盤參考 —— 加密貨幣的個別標的大多跟著 BTC 走,
    個股看起來是多頭但 BTC 正在崩的時候,那個多頭訊號要打折。
    """
    if not indicators.data_ok:
        return MarketRegime(
            reasons=[f"資料不可信: {indicators.data_error}"],
        )

    adx = indicators.adx
    atr_pct = indicators.atr_pct
    volatility = classify_volatility(atr_pct)
    reasons = []

    if adx is None or indicators.ema20 is None or indicators.ema50 is None:
        return MarketRegime(
            volatility=volatility, adx=adx, atr_pct=atr_pct,
            reasons=["缺少判斷市況所需的指標"],
        )

    bullish = indicators.ema20 > indicators.ema50

    # ADX < 20 是盤整的慣例門檻。盤整市裡 EMA 的方向沒有意義。
    if adx < 20:
        regime = Regime.RANGE
        reasons.append(f"ADX {adx:.1f} 低於 20,趨勢不成形")
    elif adx >= 40:
        regime = Regime.STRONG_BULL if bullish else Regime.STRONG_BEAR
        reasons.append(f"ADX {adx:.1f} 顯示強趨勢")
    else:
        regime = Regime.BULL if bullish else Regime.BEAR
        reasons.append(f"ADX {adx:.1f} 趨勢成形")

    if regime.is_trending:
        reasons.append(
            f"EMA20 {'高於' if bullish else '低於'} EMA50"
        )

    # 大盤反向時降一級 —— 逆著 BTC 做需要更強的理由
    if btc_indicators is not None and btc_indicators.data_ok:
        btc_bullish = btc_indicators.trend == "BULLISH"

        if regime.is_bullish and not btc_bullish:
            regime = Regime.RANGE if regime is Regime.BULL else Regime.BULL
            reasons.append("BTC 偏空,個別標的的多頭訊號降級")
        elif regime.is_bearish and btc_bullish:
            regime = Regime.RANGE if regime is Regime.BEAR else Regime.BEAR
            reasons.append("BTC 偏多,個別標的的空頭訊號降級")

    if volatility is Volatility.EXTREME:
        reasons.append(
            f"ATR 佔價格 {atr_pct:.2f}%,極端波動下停損容易被無意義地掃到"
        )

    return MarketRegime(
        regime=regime, volatility=volatility,
        adx=adx, atr_pct=atr_pct, reasons=reasons,
    )
