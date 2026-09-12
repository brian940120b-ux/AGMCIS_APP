"""
市場結構分析(Master Prompt 第二十五節)。

EMA、RSI、MACD 這些指標回答的是「動能往哪邊」。市場結構回答的是
另一個問題:**價格正在哪裡交易,以及它在那裡做過什麼。**

一根跌破前低的 K 棒與一根跌到同樣價位但那裡沒有前低的 K 棒,
在 RSI 眼裡完全一樣。在市場結構眼裡是兩件事。

## 這個模組刻意不做的事

**不預測。** 它只描述已經發生的結構:哪裡是擺動高低點、趨勢是
HH/HL 還是 LH/LL、最近有沒有掃過流動性。要不要據此交易是策略的事。

**不在資料不足時給答案。** 每一個函式在樣本不夠時回 None 而不是
一個看起來合理的數字。一個用 5 根 K 棒算出來的「支撐位」不是支撐位,
它只是那 5 根裡的最低價。

## 擺動點的定義

一個擺動高點是「左右各 N 根都比它低」的那根 K 棒的高點。N 越大,
點越少但越可靠。這裡的預設是 2 —— 再大會在 1 小時線上少到無法判斷結構。

**最後 N 根永遠不會被判定為擺動點**,因為它右邊的 K 棒還沒發生。
這一點很重要:允許最後一根成為擺動點,等於用未來資料判斷現在,
而那正是第三十四節禁止的事。
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger("agmcis.analysis.structure")

DEFAULT_SWING_STRENGTH = 2

# 少於這麼多根就不做結構判斷。
MIN_CANDLES = 20

# 掃蕩的定義:穿過前高 / 前低超過這個比例才算真的穿過,
# 而不是浮點誤差或一個 tick 的雜訊。
SWEEP_THRESHOLD_PCT = 0.05

# 趨勢結構
UPTREND = "UPTREND"          # HH + HL
DOWNTREND = "DOWNTREND"      # LH + LL
RANGE = "RANGE"
UNKNOWN = "UNKNOWN"


def _rows(candles):
    """接受 dict 或 [time, o, h, l, c, v]。回不出來就回空 list。"""
    rows = []
    for candle in candles or []:
        try:
            if isinstance(candle, dict):
                rows.append({
                    "high": float(candle["high"]),
                    "low": float(candle["low"]),
                    "close": float(candle["close"]),
                    "volume": float(candle.get("volume") or 0.0),
                })
            else:
                rows.append({
                    "high": float(candle[2]), "low": float(candle[3]),
                    "close": float(candle[4]),
                    "volume": float(candle[5]) if len(candle) > 5 else 0.0,
                })
        except (KeyError, IndexError, TypeError, ValueError):
            return []
    return rows


@dataclass
class SwingPoint:
    index: int
    price: float
    kind: str       # "high" 或 "low"

    def to_dict(self):
        return {"index": self.index, "price": round(self.price, 8), "kind": self.kind}


def swing_points(candles, strength=DEFAULT_SWING_STRENGTH):
    """
    找出擺動高低點。

    最後 `strength` 根不會被判定 —— 它們右邊的 K 棒還沒發生。
    允許它們成為擺動點等於用未來資料判斷現在。
    """
    rows = _rows(candles)
    if len(rows) < strength * 2 + 1:
        return []

    points = []

    for i in range(strength, len(rows) - strength):
        window = rows[i - strength: i + strength + 1]
        high = rows[i]["high"]
        low = rows[i]["low"]

        if all(high >= c["high"] for c in window) and any(
            high > c["high"] for c in window
        ):
            points.append(SwingPoint(index=i, price=high, kind="high"))

        if all(low <= c["low"] for c in window) and any(
            low < c["low"] for c in window
        ):
            points.append(SwingPoint(index=i, price=low, kind="low"))

    return points


@dataclass
class Structure:
    trend: str = UNKNOWN
    highs: List[float] = field(default_factory=list)
    lows: List[float] = field(default_factory=list)
    higher_high: Optional[bool] = None
    higher_low: Optional[bool] = None
    lower_high: Optional[bool] = None
    lower_low: Optional[bool] = None
    support: Optional[float] = None
    resistance: Optional[float] = None
    detail: str = ""

    def to_dict(self):
        return {
            "trend": self.trend,
            "higher_high": self.higher_high,
            "higher_low": self.higher_low,
            "lower_high": self.lower_high,
            "lower_low": self.lower_low,
            "support": round(self.support, 8) if self.support else None,
            "resistance": round(self.resistance, 8) if self.resistance else None,
            "swing_highs": [round(h, 8) for h in self.highs[-4:]],
            "swing_lows": [round(low, 8) for low in self.lows[-4:]],
            "detail": self.detail,
        }


def analyse(candles, strength=DEFAULT_SWING_STRENGTH):
    """
    HH / HL / LH / LL 與最近的支撐壓力。

    需要至少兩個擺動高點與兩個擺動低點才給趨勢判定 ——
    一個高點比不出「更高的高點」。
    """
    structure = Structure()
    rows = _rows(candles)

    if len(rows) < MIN_CANDLES:
        structure.detail = f"只有 {len(rows)} 根 K 棒(需要 {MIN_CANDLES}),不做結構判斷"
        return structure

    points = swing_points(candles, strength=strength)
    structure.highs = [p.price for p in points if p.kind == "high"]
    structure.lows = [p.price for p in points if p.kind == "low"]

    # 支撐 / 壓力 = 最近的擺動低 / 高。不是「整段的最高最低」——
    # 那兩個數字通常在很久以前,對現在的價格沒有意義。
    if structure.lows:
        structure.support = structure.lows[-1]
    if structure.highs:
        structure.resistance = structure.highs[-1]

    if len(structure.highs) < 2 or len(structure.lows) < 2:
        structure.detail = (
            f"擺動點不足(高點 {len(structure.highs)} 個、"
            f"低點 {len(structure.lows)} 個),各需要 2 個才比得出結構"
        )
        return structure

    structure.higher_high = structure.highs[-1] > structure.highs[-2]
    structure.lower_high = not structure.higher_high
    structure.higher_low = structure.lows[-1] > structure.lows[-2]
    structure.lower_low = not structure.higher_low

    if structure.higher_high and structure.higher_low:
        structure.trend = UPTREND
        structure.detail = "更高的高點 + 更高的低點"
    elif structure.lower_high and structure.lower_low:
        structure.trend = DOWNTREND
        structure.detail = "更低的高點 + 更低的低點"
    else:
        structure.trend = RANGE
        structure.detail = "高低點方向不一致,結構上是盤整"

    return structure


@dataclass
class Sweep:
    """
    穿越前一個擺動高 / 低之後發生了什麼。

    `swept` 與 `broke_out` 是互斥的兩種結果,而且**只有 swept 有交易意義**:

        穿過去 + 收回原本那一側  ->  swept(假突破,停損被吃掉但價格沒跟上)
        穿過去 + 站在新的一側    ->  broke_out(結構真的改變了)

    一開始我把兩者都算成 swept,結果在任何一段乾淨的上升趨勢裡,
    每一根都會回報「掃蕩」—— 因為每一根都在創新高。那個訊號沒有資訊量。
    """
    swept: bool = False
    broke_out: bool = False
    direction: Optional[str] = None      # "high" = 穿越上方
    level: Optional[float] = None
    reclaimed: bool = False
    detail: str = ""

    @property
    def penetrated(self):
        return self.swept or self.broke_out

    def to_dict(self):
        return {
            "swept": self.swept,
            "broke_out": self.broke_out,
            "penetrated": self.penetrated,
            "direction": self.direction,
            "level": round(self.level, 8) if self.level else None,
            "reclaimed": self.reclaimed,
            "detail": self.detail,
        }


def liquidity_sweep(candles, strength=DEFAULT_SWING_STRENGTH,
                    threshold_pct=SWEEP_THRESHOLD_PCT):
    """
    流動性掃蕩:最後一根穿過了前一個擺動高 / 低,然後又收回來。

    「穿過又收回來」與「穿過並站穩」是完全不同的兩件事:
    前者代表那些停損被吃掉之後價格沒有跟上(假突破),
    後者代表結構真的改變了。所以只有前者算 swept ——
    把兩者都算成掃蕩的話,任何一段乾淨的上升趨勢裡每一根都會
    回報掃蕩,因為每一根都在創新高。
    """
    sweep = Sweep()
    rows = _rows(candles)

    if len(rows) < MIN_CANDLES:
        sweep.detail = f"只有 {len(rows)} 根 K 棒,不做掃蕩判斷"
        return sweep

    # 最後一根不參與擺動點判定,所以拿它跟**它之前**的擺動點比
    points = swing_points(rows[:-1], strength=strength)
    last = rows[-1]

    highs = [p.price for p in points if p.kind == "high"]
    lows = [p.price for p in points if p.kind == "low"]

    if highs:
        level = highs[-1]
        if last["high"] > level * (1 + threshold_pct / 100.0):
            sweep.direction = "high"
            sweep.level = level
            sweep.reclaimed = last["close"] < level
            sweep.swept = sweep.reclaimed
            sweep.broke_out = not sweep.reclaimed
            sweep.detail = (
                f"穿過前高 {level:.8g} 後"
                f"{'收回下方(假突破)' if sweep.reclaimed else '站穩上方(突破)'}"
            )
            return sweep

    if lows:
        level = lows[-1]
        if last["low"] < level * (1 - threshold_pct / 100.0):
            sweep.direction = "low"
            sweep.level = level
            sweep.reclaimed = last["close"] > level
            sweep.swept = sweep.reclaimed
            sweep.broke_out = not sweep.reclaimed
            sweep.detail = (
                f"穿過前低 {level:.8g} 後"
                f"{'收回上方(假跌破)' if sweep.reclaimed else '站穩下方(跌破)'}"
            )
            return sweep

    sweep.detail = "最後一根沒有穿過任何擺動高低點"
    return sweep


def vwap(candles, window=None):
    """
    成交量加權平均價。

    量全部是 0 時回 None,**不退化成簡單平均** —— 那會變成一個
    叫 VWAP 但其實是 SMA 的數字,而讀的人不會知道。
    """
    rows = _rows(candles)
    if not rows:
        return None

    if window:
        rows = rows[-int(window):]

    total_volume = sum(r["volume"] for r in rows)
    if total_volume <= 0:
        return None

    typical = sum(
        (r["high"] + r["low"] + r["close"]) / 3.0 * r["volume"] for r in rows
    )
    return round(typical / total_volume, 8)


@dataclass
class VolumeProfile:
    poc: Optional[float] = None          # 成交量最大的價格區間
    value_area_high: Optional[float] = None
    value_area_low: Optional[float] = None
    bins: List[dict] = field(default_factory=list)
    detail: str = ""

    def to_dict(self):
        return {
            "poc": round(self.poc, 8) if self.poc else None,
            "value_area_high": (
                round(self.value_area_high, 8) if self.value_area_high else None
            ),
            "value_area_low": (
                round(self.value_area_low, 8) if self.value_area_low else None
            ),
            "detail": self.detail,
        }


def volume_profile(candles, bins=24, value_area_pct=70.0):
    """
    成交量分佈。POC 是成交量最大的價格區間,價值區是涵蓋
    `value_area_pct` 成交量的範圍。

    ⚠️ 這是**用 K 棒近似的**成交量分佈:把每根 K 棒的量平均分配到
    它的高低範圍內。真正的成交量分佈需要逐筆或逐檔資料。
    近似的方向是把量抹平,所以 POC 會比真實的更靠近區間中心。
    """
    profile = VolumeProfile()
    rows = _rows(candles)

    if len(rows) < MIN_CANDLES:
        profile.detail = f"只有 {len(rows)} 根 K 棒,不做成交量分佈"
        return profile

    top = max(r["high"] for r in rows)
    bottom = min(r["low"] for r in rows)

    if top <= bottom:
        profile.detail = "價格完全沒有波動,分佈沒有意義"
        return profile

    if sum(r["volume"] for r in rows) <= 0:
        profile.detail = "沒有成交量資料"
        return profile

    width = (top - bottom) / bins
    buckets = [0.0] * bins

    for row in rows:
        low_bin = min(bins - 1, max(0, int((row["low"] - bottom) / width)))
        high_bin = min(bins - 1, max(0, int((row["high"] - bottom) / width)))
        span = high_bin - low_bin + 1
        share = row["volume"] / span
        for b in range(low_bin, high_bin + 1):
            buckets[b] += share

    total = sum(buckets)
    poc_index = max(range(bins), key=lambda i: buckets[i])
    profile.poc = round(bottom + (poc_index + 0.5) * width, 8)

    # 從 POC 往兩邊擴張,直到涵蓋 value_area_pct 的量
    target = total * value_area_pct / 100.0
    covered = buckets[poc_index]
    low_index = high_index = poc_index

    while covered < target and (low_index > 0 or high_index < bins - 1):
        below = buckets[low_index - 1] if low_index > 0 else -1
        above = buckets[high_index + 1] if high_index < bins - 1 else -1

        if above >= below:
            high_index += 1
            covered += buckets[high_index]
        else:
            low_index -= 1
            covered += buckets[low_index]

    profile.value_area_low = round(bottom + low_index * width, 8)
    profile.value_area_high = round(bottom + (high_index + 1) * width, 8)
    profile.detail = (
        f"{bins} 個價格區間,價值區涵蓋 {covered / total * 100:.0f}% 成交量"
    )
    return profile
