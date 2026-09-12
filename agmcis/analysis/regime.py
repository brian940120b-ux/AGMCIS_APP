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
    # PANIC 是一個**獨立的市況**,不是「很嚴重的 STRONG_BEAR」。
    #
    # 差別在於:STRONG_BEAR 裡順勢做空是合理的,PANIC 裡不是 ——
    # 極端波動下停損會被無意義地掃到、點差會擴大到吃掉優勢、
    # 而且反彈的幅度與速度跟趨勢市完全不同。
    # 把它歸進 STRONG_BEAR 會讓系統在最不該交易的時候最積極。
    PANIC = "PANIC"
    UNKNOWN = "UNKNOWN"

    @property
    def is_trending(self):
        return self in (
            Regime.STRONG_BULL, Regime.BULL, Regime.BEAR, Regime.STRONG_BEAR
        )

    @property
    def is_panic(self):
        return self is Regime.PANIC

    @property
    def is_bullish(self):
        return self in (Regime.STRONG_BULL, Regime.BULL)

    @property
    def is_bearish(self):
        return self in (Regime.STRONG_BEAR, Regime.BEAR)


class RiskAppetite(str, Enum):
    """
    風險偏好(第二十三節的 RISK_ON / RISK_OFF)。

    它與 Regime 是**兩個不同的維度**,不是同一個列舉的更多選項:
    一個標的可以在 BULL 市況但市場整體 RISK_OFF(資金往 BTC 集中,
    山寨幣失血),那是兩件同時成立的事。把它們塞進同一個列舉,
    就得在「這檔是 BULL 還是 RISK_OFF」之間二選一,而正確答案是「都是」。
    """
    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


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

# PANIC 的門檻:極端波動 **加上** 明確的下跌方向。
# 只有極端波動不算 panic —— 暴漲的波動一樣極端,但那是另一回事。
PANIC_ATR_PCT = 8.0


@dataclass
class MarketRegime:
    regime: Regime = Regime.UNKNOWN
    volatility: Volatility = Volatility.UNKNOWN
    # 風險偏好是獨立的維度,見 RiskAppetite 的說明。
    risk_appetite: "RiskAppetite" = None
    adx: Optional[float] = None
    atr_pct: Optional[float] = None
    reasons: List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.risk_appetite is None:
            self.risk_appetite = RiskAppetite.UNKNOWN

    @property
    def is_tradeable(self):
        """
        UNKNOWN 不可交易。極端波動也不可交易 ——
        那種環境下停損很容易被無意義地掃到。
        PANIC 同理,而且更明確。
        """
        return (
            self.regime is not Regime.UNKNOWN
            and self.regime is not Regime.PANIC
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


def classify_risk_appetite(indicators, btc_indicators=None):
    """
    RISK_ON / RISK_OFF(第二十三節)。

    用「這檔相對 BTC 的表現」判斷,而不是它自己的漲跌:
    加密貨幣的資金在 risk-off 的時候會往 BTC 集中,山寨幣就算沒跌
    也會相對走弱。看絕對漲跌看不出這件事。

    沒有 BTC 參考就回 UNKNOWN —— 沒有基準就沒有「相對」可言,
    回 NEUTRAL 會假裝我們知道一件其實不知道的事。
    """
    if btc_indicators is None or not btc_indicators.data_ok:
        return RiskAppetite.UNKNOWN

    if not indicators.data_ok:
        return RiskAppetite.UNKNOWN

    btc_bullish = btc_indicators.trend == "BULLISH"
    own_bullish = indicators.trend == "BULLISH"

    if own_bullish and btc_bullish:
        return RiskAppetite.RISK_ON
    if not own_bullish and not btc_bullish:
        return RiskAppetite.RISK_OFF

    # 一個多一個空:資金在移動,但方向說不上是進場還是避險。
    return RiskAppetite.NEUTRAL


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

    # ---- PANIC:極端波動 + 明確下跌 ----
    #
    # 放在最後、而且會覆蓋前面的判定:panic 不是一種趨勢強度,
    # 它是一個「這裡的規則不一樣了」的狀態。順勢做空在 STRONG_BEAR
    # 是合理的,在 PANIC 不是 —— 停損會被掃、點差會擴大、
    # 反彈的幅度與速度跟趨勢市完全不同。
    if atr_pct is not None and atr_pct >= PANIC_ATR_PCT and not bullish:
        regime = Regime.PANIC
        reasons.append(
            f"ATR 佔價格 {atr_pct:.2f}% 且趨勢向下 —— 這不是強空頭,是恐慌。"
            f"順勢做空在這種環境下的勝率與趨勢市完全不同。"
        )

    return MarketRegime(
        regime=regime, volatility=volatility,
        risk_appetite=classify_risk_appetite(indicators, btc_indicators),
        adx=adx, atr_pct=atr_pct, reasons=reasons,
    )
